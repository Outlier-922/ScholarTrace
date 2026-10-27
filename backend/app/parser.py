"""Versioned native/OCR parsing with source rectangles and explicit limits."""

from __future__ import annotations

import hashlib
import logging
import statistics
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

import pymupdf as fitz

from . import ocr, store

logger = logging.getLogger(__name__)

ARXIV = re.compile(r"\barxiv\s*:\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)
HEADING_WORDS = re.compile(r"^(abstract|introduction|background|related work|method(?:s|ology)?|"
                           r"experiments?|results?|discussion|conclusions?|references|bibliography|"
                           r"appendix(?:\s+[A-Z])?|acknowledg(?:e)?ments?|摘要|引言|参考文献|附录)\s*$", re.I)


def _normalized_bbox(bbox: list[float] | tuple[float, ...], width: float, height: float) -> list[float]:
    return [round(min(1.0, max(0.0, float(v) / (width if i % 2 == 0 else height))), 6)
            for i, v in enumerate(bbox)]


def _reading_order(blocks: list[dict[str, Any]], width: float) -> list[dict[str, Any]]:
    """Separate likely columns inside bands delimited by full-width content."""
    if not blocks:
        return []
    middle = width / 2
    left = [b for b in blocks if b["rect"][2] <= middle + width * .03]
    right = [b for b in blocks if b["rect"][0] >= middle - width * .03]
    if len(left) < 2 or len(right) < 2:
        return sorted(blocks, key=lambda b: (round(b["rect"][1] / 4), b["rect"][0]))
    full = sorted([b for b in blocks if b not in left and b not in right], key=lambda b: b["rect"][1])
    remaining = [b for b in blocks if b not in full]
    ordered: list[dict[str, Any]] = []
    for separator in full:
        band = [b for b in remaining if b["rect"][1] < separator["rect"][1]]
        band.sort(key=lambda b: (0 if (b["rect"][0] + b["rect"][2]) / 2 < middle else 1, b["rect"][1]))
        ordered.extend(band)
        ordered.append(separator)
        remaining = [b for b in remaining if b not in band]
    remaining.sort(key=lambda b: (0 if (b["rect"][0] + b["rect"][2]) / 2 < middle else 1, b["rect"][1]))
    return ordered + remaining


def _extract_raw(page: fitz.Page) -> list[dict[str, Any]]:
    raw = []
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES).get("blocks", []):
        if block.get("type") != 0:
            continue
        lines, sizes, flags = [], [], []
        for line in block.get("lines", []):
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))
            if line_text.strip():
                lines.append(line_text.strip())
            for span in line.get("spans", []):
                if span.get("text", "").strip():
                    sizes.extend([float(span.get("size", 10))] * min(100, len(span["text"])))
                    flags.append(int(span.get("flags", 0)))
        text = "\n".join(lines).strip()
        if text:
            raw.append({"text": text, "rect": list(block["bbox"]), "font_size": max(sizes) if sizes else 10,
                        "body_size": Counter(round(s, 1) for s in sizes).most_common(1)[0][0] if sizes else 10,
                        "bold": bool(flags) and sum(bool(flag & 16) for flag in flags) >= len(flags) / 2})
    return raw


def _body_font(pdf: fitz.Document) -> float:
    sizes: Counter[float] = Counter()
    for i in range(min(len(pdf), 4)):
        for block in _extract_raw(pdf[i]):
            sizes[block["body_size"]] += min(len(block["text"]), 1000)
    return sizes.most_common(1)[0][0] if sizes else 10


def _ocr_paragraphs(lines: list[dict[str, Any]], width: float, height: float) -> list[dict[str, Any]]:
    """Group adjacent same-column OCR lines, retaining every original line box."""
    ordered = _reading_order(lines, width)
    grouped: list[dict[str, Any]] = []
    body_size = statistics.median([line["font_size"] for line in lines]) if lines else 10
    for line in ordered:
        line_record = {"text": line["text"], "bbox": _normalized_bbox(line["rect"], width, height),
                       "confidence": line["confidence"]}
        level = _heading_level(line["text"], line["font_size"], body_size, False)
        previous = grouped[-1] if grouped else None
        merge = False
        if previous and not level and not previous.get("ocr_heading"):
            a, b = previous["rect"], line["rect"]
            previous_height = max(1.0, previous["last_height"])
            same_column = abs(a[0] - b[0]) < max(18, width * .025) and (a[0] < width / 2) == (b[0] < width / 2)
            gap = b[1] - a[3]
            similar_size = .8 <= line["font_size"] / max(previous["font_size"], 1) <= 1.2
            new_reference = bool(re.match(r"^\[\d+\]", line["text"]))
            merge = same_column and -previous_height * .15 <= gap <= previous_height * .9 and similar_size and not new_reference
        if merge:
            previous["text"] += "\n" + line["text"]
            previous["rect"] = [min(previous["rect"][0], line["rect"][0]), min(previous["rect"][1], line["rect"][1]),
                                max(previous["rect"][2], line["rect"][2]), max(previous["rect"][3], line["rect"][3])]
            previous["ocr_lines"].append(line_record)
            previous["confidence"] = sum(item["confidence"] for item in previous["ocr_lines"]) / len(previous["ocr_lines"])
            previous["last_height"] = line["rect"][3] - line["rect"][1]
        else:
            grouped.append({**line, "ocr_lines": [line_record], "ocr_heading": bool(level),
                            "body_size": body_size, "last_height": line["rect"][3] - line["rect"][1]})
    return grouped


def _guess_title(pdf: fitz.Document, first: list[dict[str, Any]]) -> str | None:
    metadata_title = (pdf.metadata or {}).get("title", "").strip()
    if metadata_title and len(metadata_title) > 4 and not re.search(r"^(untitled|microsoft|latex|document|word|powerpoint)", metadata_title, re.I):
        return re.sub(r"\s+", " ", metadata_title)[:500]
    candidates = [block for block in first if 8 <= len(block["text"]) <= 500
                  and block["rect"][1] < pdf[0].rect.height * .48
                  and not ARXIV.search(block["text"])
                  and not re.match(r"https?://|^\d+$|^proceedings", block["text"], re.I)]
    if not candidates:
        return None
    candidate = max(candidates, key=lambda b: (b["font_size"], -b["rect"][1]))
    return re.sub(r"\s+", " ", candidate["text"])[:500]


def _heading_level(text: str, font_size: float, body_size: float, bold: bool) -> int | None:
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) > 180 or len(text.splitlines()) > 3:
        return None
    number = re.match(r"^(\d+(?:\.\d+){0,3})\.?\s+\S", flat)
    if number and len(flat) < 140 and not re.match(r"^\d+\s*[%=×+−-]", flat):
        segments = number.group(1).split(".")
        remainder = flat[number.end() - 1:]
        layout_signal = bold or font_size >= body_size * 1.05
        title_signal = bool(remainder and (remainder[0].isupper() or "\u4e00" <= remainder[0] <= "\u9fff"))
        if all(len(segment) <= 2 for segment in segments) and int(segments[0]) > 0 and (layout_signal or title_signal):
            return min(3, len(segments))
    if HEADING_WORDS.match(flat):
        return 1
    if len(flat) < 120 and font_size >= body_size * 1.13 and not flat.endswith((".", ",", ";")):
        return 1 if font_size >= body_size * 1.45 else 2
    if bold and len(flat) < 75 and len(flat.split()) < 12 and not flat.endswith((".", ",", ";")):
        return 2
    return None


def _classify(text: str, level: int | None, title: str | None, page_number: int) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    if page_number == 1 and title and SequenceMatcher(None, flat.casefold(), title.casefold()).ratio() > .92:
        return "title"
    if level:
        return "heading"
    if re.match(r"^(figure|fig\.|图)\s*\d", flat, re.I):
        return "figure_caption"
    if re.match(r"^(table|表)\s*\d", flat, re.I):
        return "table_caption"
    math_symbols = len(re.findall(r"[=∑∏∫√∂∇≈≤≥∈∉⊂⊆∞αβγδεθλμσφω]", text))
    if math_symbols >= 2 and len(flat.split()) < 45:
        return "formula_candidate"
    if len(text.splitlines()) >= 3 and sum(bool(re.search(r"\d", line)) for line in text.splitlines()) >= 3:
        if len(re.findall(r"\d+(?:\.\d+)?", text)) >= 8 and len(flat.split()) < 130:
            return "table_candidate"
    return "paragraph"


def _references(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result, inside = [], False
    for block in blocks:
        text = block["text"].strip()
        if block["type"] == "heading" and re.match(r"^(?:\d+\.?\s*)?(references|bibliography|参考文献)\s*$", text, re.I):
            inside = True
            continue
        if inside and block["type"] == "heading" and re.match(r"appendix|附录|acknowledg", text, re.I):
            inside = False
        if not inside or not text:
            continue
        # A block may contain several numbered references. Keep original text.
        entries = re.split(r"(?m)(?=^\s*\[\d+\]\s)", text)
        for entry in entries:
            entry = entry.strip()
            if len(entry) < 12:
                continue
            marker = re.match(r"^\s*\[(\d+)\]", entry)
            doi = re.search(r"\b10\.\d{4,9}/[^\s<>]+", entry)
            arxiv = ARXIV.search(entry)
            result.append({"text": entry, "marker": marker.group(1) if marker else None,
                           "page": block["page"], "block_id": block["id"],
                           "doi": doi.group().rstrip(".,;") if doi else None,
                           "arxiv_id": arxiv.group(1) if arxiv else None})
    return result


def _fingerprint(text: str) -> tuple[str, list[str]]:
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.casefold())
    shingles = {" ".join(tokens[i:i + 5]) for i in range(max(0, len(tokens) - 4))}
    # Bottom-k hashes bound SQLite footprint for long books, deterministically.
    hashes = sorted({hashlib.blake2b(shingle.encode(), digest_size=8).hexdigest() for shingle in shingles})[:2048]
    return hashlib.sha256(" ".join(tokens).encode()).hexdigest(), hashes


def _detect_duplicate(document_id: str, title: str, arxiv_id: str | None,
                      version: str | None, hashes: list[str],
                      candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    hash_set = set(hashes)
    best = None
    best_score = 0.0
    for candidate in candidates if candidates is not None else store.dedup_candidates(document_id):
        identity_match = bool(arxiv_id and candidate["arxiv_id"] == arxiv_id)
        title_similarity = SequenceMatcher(None, re.sub(r"\W", "", title.casefold()),
                                            re.sub(r"\W", "", candidate["title"].casefold())).ratio()
        other = set(candidate["fingerprint_tokens"])
        # Compare common bottom-k region; unequal document sizes need this
        # common threshold to keep the sketch intersection meaningful.
        if hash_set and other:
            threshold = min(max(hash_set), max(other))
            a, b = {h for h in hash_set if h <= threshold}, {h for h in other if h <= threshold}
            content_overlap = len(a & b) / max(1, len(a | b))
        else:
            content_overlap = 0
        same_content = len(hash_set) >= 8 and content_overlap >= .78 and title_similarity >= .65
        if not identity_match and not same_content:
            continue
        score = 2 if identity_match else content_overlap
        if score > best_score:
            best, best_score = candidate, score
    if not best:
        return {}
    kind = "same_work_version" if version and best["version_label"] and version != best["version_label"] else "content_overlap"
    recommendation = "正文高度重合，已归为同一作品候选；两个文件均已保留。版本先后尚需核实。"
    if version and best["version_label"]:
        new_num = int(re.sub(r"\D", "", version) or 0)
        old_num = int(re.sub(r"\D", "", best["version_label"]) or 0)
        if new_num < old_num:
            recommendation = f"库中已有较新版本 {best['version_label']}，建议继续使用该版本；本次 {version} 已保留供比较。"
        elif new_num > old_num:
            recommendation = f"本次 {version} 比库中 {best['version_label']} 更新，建议阅读新版；旧文件继续保留。"
        else:
            recommendation = f"检测到同一作品的 {version} 文件，建议核查排版或来源差异；两个文件均保留。"
    return {"duplicate_of": best["id"], "duplicate_kind": kind,
            "version_recommendation": recommendation, "work_id": best["work_id"]}


def parse_document(document_id: str, expected_run_id: str | None = None) -> None:
    document = store.get_internal_document(document_id)
    if not document or (expected_run_id and document["parse_run_id"] != expected_run_id):
        return
    run_id, job_id = document["parse_run_id"], document["job_id"]
    run = store.get_parse_run(document_id, run_id)
    config = (run or {}).get("config", {})
    perform_ocr = bool(config.get("ocr", False))
    perform_tables = bool(config.get("tables", False))
    store.update_document(document_id, status="processing", error=None)
    store.set_run_status(run_id, "processing")
    store.update_job(job_id, "processing", "正在提取原生文字与页面坐标")
    try:
        with fitz.open(document["file_path"]) as pdf:
            existing = store.get_structure(document_id, parse_run_id=run_id) or {"pages": [], "blocks": []}
            completed = {p["number"] for p in existing["pages"]}
            first = _extract_raw(pdf[0])
            first_ocr: list[dict[str, Any]] | None = None
            ocr_errors: list[int] = []
            table_errors: list[int] = []
            stored_ocr = [block for block in existing["blocks"] if block["page"] == 1 and block["text"]
                          and block.get("metadata", {}).get("extraction") == "rapidocr"]
            if 1 in completed and stored_ocr:
                width, height = float(pdf[0].cropbox.width), float(pdf[0].cropbox.height)
                first_ocr = [{"text": block["text"],
                              "rect": [coordinate * (width if i % 2 == 0 else height) for i, coordinate in enumerate(block["bbox"])],
                              "font_size": block["metadata"].get("font_size", 10), "bold": False}
                             for block in stored_ocr]
                first = first_ocr
            if 1 not in completed and perform_ocr and sum(len(block["text"]) for block in first) < 20 and pdf[0].get_image_info():
                try:
                    first_ocr = _ocr_paragraphs(ocr.extract_ocr(pdf[0]), float(pdf[0].cropbox.width), float(pdf[0].cropbox.height))
                    if first_ocr:
                        first = first_ocr
                except Exception:
                    first_ocr = []
                    ocr_errors.append(1)
                    logger.warning("OCR unavailable for document %s page 1", document_id, exc_info=True)
            title = _guess_title(pdf, first) or document["title"]
            author_text = (pdf.metadata or {}).get("author", "").strip()
            authors = [item.strip() for item in re.split(r";|\n", author_text) if item.strip()]
            first_page_text = "\n".join(b["text"] for b in first)
            identity = ARXIV.search(first_page_text[:5000])
            arxiv_id = identity.group(1) if identity else None
            version = identity.group(2).lower() if identity and identity.group(2) else None
            store.update_document(document_id, title=title, authors=authors, page_count=len(pdf),
                                  arxiv_id=arxiv_id, version_label=version)
            body_size = statistics.median([b["font_size"] for b in first]) if first_ocr else _body_font(pdf)
            sections: list[str] = []
            for index, page in enumerate(pdf):
                current = store.get_document(document_id)
                if not current or current["parse_run_id"] != run_id:
                    return
                number = index + 1
                if number in completed:
                    old = [b for b in existing["blocks"] if b["page"] == number and b["type"] == "heading"]
                    for heading in old:
                        level = heading.get("heading_level") or 1
                        sections = sections[:level - 1] + [heading["text"]]
                    continue
                # Text coordinates are unrotated crop coordinates; use matching
                # dimensions and keep page rotation separately for the viewer.
                width, height = float(page.cropbox.width), float(page.cropbox.height)
                raw = _reading_order(_extract_raw(page), width)
                used_ocr = False
                image_info = page.get_image_info()
                if perform_ocr and sum(len(value["text"]) for value in raw) < 20 and image_info:
                    try:
                        recognized = first_ocr if number == 1 and first_ocr is not None else _ocr_paragraphs(ocr.extract_ocr(page), width, height)
                        if recognized:
                            raw, used_ocr = recognized, True
                    except Exception:
                        ocr_errors.append(number)
                        logger.warning("OCR failed for document %s page %s", document_id, number, exc_info=True)
                blocks = []
                for order, value in enumerate(raw):
                    page_body_size = value.get("body_size", body_size) if used_ocr else body_size
                    level = _heading_level(value["text"], value["font_size"], page_body_size, value["bold"])
                    kind = _classify(value["text"], level, title, number)
                    if kind == "title":
                        level = 1
                    if kind == "heading":
                        sections = sections[:(level or 1) - 1] + [value["text"]]
                    metadata = {"extraction": "rapidocr" if used_ocr else "native_pdf", "font_size": round(value["font_size"], 2),
                                "is_candidate": kind.endswith("_candidate")}
                    if used_ocr:
                        metadata.update(confidence=round(value["confidence"], 6), lines=value.get("ocr_lines", []),
                                        low_confidence=value["confidence"] < .8, verification_status="ocr_unverified")
                    blocks.append({"id": store.new_id("blk"), "page": number, "type": kind,
                                   "text": value["text"], "bbox": _normalized_bbox(value["rect"], width, height),
                                   "order": order, "heading_level": level if kind in {"heading", "title"} else None,
                                   "section_path": list(sections),
                                   "metadata": metadata})
                # Only store rectangles for figures; avoid holding image bytes.
                image_count = 0
                for image in image_info:
                    image_count += 1
                    blocks.append({"id": store.new_id("blk"), "page": number, "type": "figure",
                                   "text": "", "bbox": _normalized_bbox(image["bbox"], width, height),
                                   "order": len(blocks), "heading_level": None, "section_path": list(sections),
                                   "metadata": {"extraction": "image_region", "ocr_performed": used_ocr}})
                if perform_tables:
                    try:
                        from .tables import extract_tables
                        for table in extract_tables(page):
                            blocks.append({**table, "id": store.new_id("blk"), "page": number, "order": len(blocks),
                                           "heading_level": None, "section_path": list(sections)})
                    except Exception:
                        table_errors.append(number)
                        logger.warning("Table extraction failed for document %s page %s", document_id, number, exc_info=True)
                char_count = sum(len(block["text"].strip()) for block in blocks)
                status = "needs_ocr" if char_count < 20 and image_count else ("empty" if char_count == 0 else "ready")
                if used_ocr:
                    status = "ocr_ready" if char_count >= 20 else "needs_ocr"
                store.save_page(document_id, run_id, number, width, height, page.rotation,
                                list(page.cropbox), status, blocks)
                store.update_job(job_id, "processing", f"已解析 {number}/{len(pdf)} 页", progress=number)
            structure = store.get_structure(document_id, parse_run_id=run_id)
            if not structure:
                return
            references = _references(structure["blocks"])
            store.save_references(document_id, references, parse_run_id=run_id)
            in_references = False
            body = []
            for block in structure["blocks"]:
                if block["type"] == "heading":
                    in_references = bool(re.search(r"references|bibliography|参考文献", block["text"], re.I))
                if not in_references and block["type"] not in {"title", "figure"}:
                    body.append(block["text"])
            content_hash, hashes = _fingerprint("\n".join(body))
            scan_pages = [p["number"] for p in structure["pages"] if p["status"] == "needs_ocr"]
            warnings = ["公式尚未做 LaTeX 恢复；表格和参考文献结构仍需核对原文。"]
            if any(p["status"] == "ocr_ready" for p in structure["pages"]):
                warnings.append("部分文本由本地OCR识别，已保留行坐标和识别置信度；数字、符号和公式需要核对原图。")
            if scan_pages:
                warnings.append(f"{len(scan_pages)} 页缺少可用文字层，需要 OCR；这些页尚不能用于文本问答。")
            if ocr_errors:
                warnings.append(f"{len(set(ocr_errors))} 页OCR未能执行，请检查本地模型准备状态或重试。")
            if table_errors:
                warnings.append(f"{len(set(table_errors))} 页表格提取失败，正文仍可使用。")
            if not authors:
                warnings.append("PDF 元数据未提供作者；尚未自动核验作者与机构。")
            status = "partial" if scan_pages or table_errors else "ready"
            store.finalize_document(document_id, status, warnings, content_hash, hashes,
                                    lambda candidates: _detect_duplicate(document_id, title, arxiv_id, version, hashes, candidates),
                                    "解析完成；部分页面或表格需要复核" if status == "partial" else "原文解析完成",
                                    parse_run_id=run_id)
    except Exception as exc:
        logger.exception("PDF parse failed for document %s", document_id)
        message = "PDF 解析失败，请检查文件是否损坏或查看后端日志。"
        store.set_run_status(run_id, "failed")
        store.update_job(job_id, "failed", message, error=type(exc).__name__)
        current = store.get_document(document_id)
        if current and current["parse_run_id"] == run_id:
            store.update_document(document_id, status="failed", error=message)
    finally:
        if not store.get_document(document_id):
            store.cleanup_deleted_files()
