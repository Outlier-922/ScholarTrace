"""Locate figures adjacent to numbered captions on raster PDF pages."""
from __future__ import annotations

import asyncio
import re

import pymupdf
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .formula_native import check_pdf
from .formulas import page_input
from .store import get_structure

router = APIRouter(prefix="/api", tags=["scan-figures"])
VERSION = "scan-caption-layout-v3"
CAPTION = re.compile(r"^(?:figure|fig\.?|图)\s*(\d{1,4}[a-z]?)\s*[:：.．]\s*\S", re.I)
SENTENCE_END = re.compile(r"[.!?。！？][\x22'”’)]*\s*$")


def is_scan(blocks):
    return any(
        b["type"] == "figure" and b["metadata"].get("extraction") == "image_region"
        and b["bbox"][2] - b["bbox"][0] >= .88 and b["bbox"][3] - b["bbox"][1] >= .88
        for b in blocks
    )


def _overlap(box, left, right):
    return min(box[2], right) - max(box[0], left)


def _caption_sources(blocks):
    captions = []
    for block in blocks:
        match = CAPTION.match(block["text"])
        if not match or block["metadata"].get("extraction") != "rapidocr":
            continue
        left, top, right, bottom = block["bbox"]
        parts = [block]
        next_top = bottom
        # OCR can split a caption into separate blocks. Join only touching,
        # left-aligned continuation lines of an unfinished, full-width line.
        for _ in range(3):
            prior = parts[-1]
            if SENTENCE_END.search(prior["text"]) or prior["bbox"][2] - left < .22:
                break
            continuation = [
                candidate for candidate in blocks
                if candidate["type"] == "paragraph"
                and candidate["metadata"].get("extraction") == "rapidocr"
                and not CAPTION.match(candidate["text"])
                and abs(candidate["bbox"][0] - left) < .012
                and candidate["bbox"][2] <= right + .035
                and candidate["bbox"][1] > prior["bbox"][1] + .005
                and -.006 <= candidate["bbox"][1] - next_top <= .003
            ]
            if len(continuation) != 1:
                break
            part = continuation[0]
            parts.append(part)
            next_top = part["bbox"][3]
        captions.append({
            "id": block["id"], "number": match[1],
            "text": "\n".join(part["text"] for part in parts),
            "caption_block_ids": [part["id"] for part in parts],
            "caption_bbox": [min(p["bbox"][0] for p in parts), top,
                             max(p["bbox"][2] for p in parts), max(p["bbox"][3] for p in parts)],
        })
    return captions


def _search_bounds(caption, blocks):
    left, top, right, _ = caption["caption_bbox"]
    center = (left + right) / 2
    if right - left >= .46 or .41 <= center <= .59:
        left, right = .025, .975
    elif center < .5:
        left, right = .025, .52
    else:
        left, right = .48, .975

    upper_limit = .024
    for block in blocks:
        box = block["bbox"]
        if not upper_limit < box[3] < top or _overlap(box, left, right) < .10:
            continue
        if block["type"] in {"figure_caption", "table", "table_caption"}:
            upper_limit = box[3]
            continue
        words = block["text"].split()
        if len(words) >= 7 and box[2] - box[0] >= .20:
            upper_limit = box[3]
            continue
        # A broad isolated title may sit above a figure. Side-by-side headings
        # can be panel labels, so allow those into the search.
        if 2 <= len(words) < 7 and box[2] - box[0] >= .16:
            parallel_label = any(
                other["id"] != block["id"] and len(other["text"].split()) >= 2
                and abs(other["bbox"][1] - box[1]) < .009
                and _overlap(other["bbox"], left, right) > .08
                for other in blocks
            )
            if not parallel_label:
                upper_limit = box[3]
    return left, upper_limit, right, top


def _figure_bbox(rgb, bounds):
    import cv2
    import numpy as np

    height, width = rgb.shape[:2]
    x0, y0, x1, y1 = (round(v * scale) for v, scale in zip(bounds, (width, height, width, height)))
    if y1 - y0 < .055 * height:
        return None
    section = rgb[y0:y1, x0:x1]
    dark = section.min(axis=2)
    ink = dark < 220
    color = (section.max(axis=2) - dark >= 17) & (dark < 233)
    kernel = np.ones((3, 3), dtype=np.uint8)
    seeds = []
    for mask in (ink, color):
        linked = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        _, _, stats, _ = cv2.connectedComponentsWithStats(linked)
        seeds.extend(
            (y, y + h) for _, y, w, h, area in stats[1:]
            if h >= .027 * height and w >= .019 * width and area >= 24
        )
    if not seeds:
        return None

    # Include nearby labels and panels in the same uninterrupted band. Long
    # blank gaps and body text delimit the search instead of filling the page.
    row_indices = np.flatnonzero(ink.sum(axis=1) >= max(3, width * .0035))
    if not row_indices.size or y1 - y0 - row_indices[-1] > .085 * height:
        return None
    gaps = np.flatnonzero(np.diff(row_indices) > .078 * height)
    start = int(row_indices[gaps[-1] + 1] if gaps.size else row_indices[0])
    end = int(row_indices[-1]) + 1
    if end - start < .052 * height or not any(start <= (a + b) / 2 <= end for a, b in seeds):
        return None
    columns = np.flatnonzero(ink[start:end].sum(axis=0) >= max(3, height * .0025))
    if not columns.size or columns[-1] - columns[0] < .07 * width:
        return None
    pad = .004
    return [max(bounds[0], (x0 + int(columns[0])) / width - pad),
            max(bounds[1], (y0 + start) / height - pad),
            min(bounds[2], (x0 + int(columns[-1]) + 1) / width + pad),
            min(bounds[3], (y0 + end) / height + pad)]


def _flip(box):
    return [box[0], 1 - box[3], box[2], 1 - box[1]]


def _find_below(rgb, caption, reverse_blocks):
    flipped_caption = {**caption, "caption_bbox": _flip(caption["caption_bbox"])}
    bounds = _search_bounds(flipped_caption, reverse_blocks)
    box = _figure_bbox(rgb[::-1], bounds)
    if box is None:
        return None
    original = _flip(box)
    # Keep a tighter gap for the less common caption-before-figure case.
    if original[1] - caption["caption_bbox"][3] > .042:
        return None
    if _overlap(original, caption["caption_bbox"][0], caption["caption_bbox"][2]) < .6 * min(
        original[2] - original[0], caption["caption_bbox"][2] - caption["caption_bbox"][0]
    ):
        return None
    return original


def _scan_pixels(page):
    import numpy as np

    if page.rotation:
        raise ValueError("rotated_scan_figure")
    scale = min(2.0, 1500 / max(page.rect.width, page.rect.height))
    bitmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(bitmap.samples, dtype=np.uint8).reshape(bitmap.height, bitmap.width, 3), scale


def _previous_figure(page, blocks, captions):
    # Only one unmatched caption at the start of the following page can
    # claim this bottom-of-page band.
    near_top = [item for item in captions if item["figure_bbox"] is None
                and item["caption_bbox"][1] <= .21]
    if len(near_top) != 1:
        return
    item = near_top[0]
    x0, _, x1, _ = item["caption_bbox"]
    reference = {**item, "caption_bbox":[x0, .955, x1, .955]}
    left, top, right, bottom = _search_bounds(reference, blocks)
    rgb, _ = _scan_pixels(page)
    box = _figure_bbox(rgb, (left, max(.55, top), right, bottom))
    if box is None or box[3] < .83 or _overlap(box, x0, x1) < .6 * min(box[2] - box[0], x1 - x0):
        return
    reverse_blocks = [{**block, "bbox": _flip(block["bbox"])} for block in blocks]
    for caption in _caption_sources(blocks):
        existing = _figure_bbox(rgb, _search_bounds(caption, blocks)) or _find_below(rgb, caption, reverse_blocks)
        if existing and _overlap(box, existing[0], existing[2]) > 0 and min(box[3], existing[3]) > max(box[1], existing[1]):
            return
    item.update(figure_bbox=box, figure_page=page.number+1, figure_relation="previous_page")


def analyze(path, sha, page_number, blocks, preview=None, previous_blocks=None):
    captions = _caption_sources(blocks)
    if not captions or len(captions) > 16:
        return [] if preview is None else None
    check_pdf(path, sha)
    with pymupdf.open(path) as pdf:
        page = pdf[page_number - 1]
        rgb, scale = _scan_pixels(page)
        reverse_blocks = [{**block, "bbox": _flip(block["bbox"])} for block in blocks]
        for item in captions:
            above = _figure_bbox(rgb, _search_bounds(item, blocks))
            below = None if above is not None else _find_below(rgb, item, reverse_blocks)
            item["figure_bbox"] = above or below
            item["figure_relation"] = "above_caption" if above else "below_caption" if below else None
            item["figure_page"] = page_number if above or below else None
        ambiguous = set()
        for index, first in enumerate(captions):
            a = first["figure_bbox"]
            if a is None:
                continue
            for other in captions[index + 1:]:
                b = other["figure_bbox"]
                if b and _overlap(a, b[0], b[2]) > 0 and min(a[3], b[3]) > max(a[1], b[1]):
                    ambiguous.update((first["id"], other["id"]))
        for item in captions:
            if item["id"] in ambiguous:
                item["figure_bbox"] = None
                item["figure_relation"] = None
                item["figure_page"] = None
        if previous_blocks and page_number > 1:
            _previous_figure(pdf[page_number - 2], previous_blocks,
                             [item for item in captions if item["id"] not in ambiguous])
        if preview is None:
            return captions
        selected = next((item for item in captions if item["id"] == preview), None)
        if not selected or selected["figure_bbox"] is None:
            return None
        if selected["figure_page"] != page_number:
            page = pdf[selected["figure_page"] - 1]
            scale = min(2.0, 1500 / max(page.rect.width, page.rect.height))
        crop = pymupdf.Rect(*(v * size for v, size in zip(
            selected["figure_bbox"], (page.rect.width, page.rect.height) * 2
        )))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=crop, alpha=False).tobytes("png")


async def _read(identity, request, page, parse_run_id, preview=None):
    doc, structure, path = page_input(identity, page, parse_run_id)
    blocks = structure["blocks"]
    if not is_scan(blocks):
        return "not_scan", []
    state = next(p["status"] for p in structure["pages"] if p["number"] == page)
    if state != "ocr_ready":
        return "needs_ocr", []
    previous_blocks = None
    if page > 1 and any(item["caption_bbox"][1] <= .21 for item in _caption_sources(blocks)):
        previous = get_structure(identity, page=page-1, parse_run_id=parse_run_id)
        if (previous and any(p["number"] == page-1 and p["status"] == "ocr_ready" and not p["rotation"]
                             for p in previous["pages"]) and is_scan(previous["blocks"])):
            previous_blocks = previous["blocks"]
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            request.app.state.parse_executor, analyze, str(path), doc["sha256"], page, blocks, preview, previous_blocks
        )
    except (ValueError, IndexError, RuntimeError, OSError, ImportError):
        raise HTTPException(422, "暂时无法定位本页图像，请检查原 PDF 和页面方向。") from None
    if not get_structure(identity, page=page, parse_run_id=parse_run_id):
        raise HTTPException(404, "原文或解析版本已不可用。")
    return "ready", result


@router.get("/documents/{identity}/scan-figures")
async def figures(identity: str, request: Request, page: int = Query(ge=1),
                  parse_run_id: str = Query(min_length=1, max_length=100)):
    status, items = await _read(identity, request, page, parse_run_id)
    return {"status": status, "items": items, "page": page, "parse_run_id": parse_run_id,
            "algorithm": VERSION}


@router.get("/documents/{identity}/scan-figures/{caption_id}/image")
async def figure_image(identity: str, caption_id: str, request: Request, page: int = Query(ge=1),
                       parse_run_id: str = Query(min_length=1, max_length=100)):
    status, png = await _read(identity, request, page, parse_run_id, caption_id)
    if status != "ready" or not png:
        raise HTTPException(404, "该图注暂无已定位的原图区域。")
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})
