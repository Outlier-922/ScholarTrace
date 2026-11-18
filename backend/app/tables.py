"""Deterministic, source-anchored PDF table candidates.

This module extracts cells, not scientific conclusions.  The default rule uses
vector grid lines; text-based detection is only available with an explicit clip
because a full page of prose can otherwise be mistaken for one large table.
PyMuPDF must run in the caller's parsing process, never in parallel threads.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Sequence

import pymupdf

MAX_PATHS = 5000
MAX_PATH_ITEMS = 30000
MAX_PAGE_EDGE = 20000
MAX_CELLS = 5000
MAX_TEXT_CHARACTERS = 500000
FIGURE_CAPTION = re.compile(r"^(?:Figure|Fig\.?|图)\s*\d+(?:[.-]\d+)?[a-z]?\s*[:.：．]\s*\S", re.I)
AXIS_DESCRIPTION = re.compile(r"\b[xy][ -]a(?:xis|xes)\b|[横纵][坐]?轴", re.I)


class TableExtractionError(RuntimeError):
    """An explicit failure/limit, distinct from a successful empty detection."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _normal_rect(rect: Sequence[float] | None, width: float, height: float) -> list[float] | None:
    if rect is None:
        return None
    values = [float(value) for value in rect]
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise TableExtractionError("invalid_geometry", "Table engine returned an invalid source rectangle")
    return [round(min(1.0, max(0.0, value / (width if index % 2 == 0 else height))), 6)
            for index, value in enumerate(values)]


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _header_labels(names: list[str | None], columns: int) -> list[str]:
    # Labels identify physical columns. Missing or repeated headings must not be
    # filled from a neighbour: that could turn an EN-FR result into EN-DE.
    raw = [(names[index] or "").strip() if index < len(names) else "" for index in range(columns)]
    counts = Counter(raw)
    return [
        (f"{name} [column {index + 1}]" if counts[name] > 1 else name)
        if name else f"[unnamed column {index + 1}]"
        for index, name in enumerate(raw)
    ]


def serialize_table(rows: list[list[str | None]], labels: list[str], *, page_number: int) -> str:
    """Make retrievable row text while preserving empty and merged-cell states."""
    lines = [f"Table candidate on PDF page {page_number}; extraction unverified.",
             "Column headings: " + " | ".join(label.replace("\n", " / ") for label in labels)]
    for row_number, row in enumerate(rows, start=1):
        fields = []
        for column, value in enumerate(row):
            rendered = "[merged or missing cell]" if value is None else ("[empty cell]" if value == "" else value.replace("\n", " / "))
            fields.append(f"{labels[column].replace(chr(10), ' / ')} = {rendered}")
        lines.append(f"Row {row_number}: " + "; ".join(fields))
    return "\n".join(lines)


def _structural_signals(rows: list[list[str | None]], names: list[str | None], page: pymupdf.Page,
                        bbox: Sequence[float]) -> dict[str, Any]:
    """Cheap negative evidence for grid-like diagrams, never a correctness score."""
    total = sum(len(row) for row in rows)
    filled = [value.strip() for row in rows for value in row if value and value.strip()]
    headers = [re.sub(r"\s+", "", value or "") for value in names]
    nontrivial_headers = sum(len(value) >= 3 and bool(re.search(r"[\w\u4e00-\u9fff]", value)) for value in headers)
    short = sum(len(re.sub(r"\s+", "", value)) <= 2 for value in filled)
    numerical = sum(bool(re.fullmatch(r"[-+−]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?%?", value)) for value in filled)
    figure_caption_on_page, nearby_table_caption = False, False
    figure_caption_below, figure_caption_mentions_axis, table_caption_below = False, False, False
    table_rect = pymupdf.Rect(bbox)
    for block in page.get_text("blocks"):
        text = str(block[4]).strip()
        caption = pymupdf.Rect(block[:4])
        overlap = min(caption.x1, table_rect.x1) - max(caption.x0, table_rect.x0)
        collinear = overlap > min(caption.width, table_rect.width) * .65
        directly_below = collinear and -2 <= caption.y0 - table_rect.y1 <= 50
        if re.match(r"^(?:Figure|Fig\.?|图)\s*\d", text, re.I):
            figure_caption_on_page = True
            if directly_below and FIGURE_CAPTION.match(text):
                figure_caption_below = True
                figure_caption_mentions_axis |= bool(AXIS_DESCRIPTION.search(text))
        if not re.match(r"^(?:Table|表)\s*[\d一二三四五六七八九十]", text, re.I):
            continue
        table_caption_below |= directly_below
        vertical_gap = min(abs(caption.y1 - table_rect.y0), abs(caption.y0 - table_rect.y1))
        if overlap > min(caption.width, table_rect.width) * .25 and vertical_gap <= 80:
            nearby_table_caption = True
    return {"filled_ratio": round(len(filled) / max(total, 1), 4),
            "nontrivial_header_ratio": round(nontrivial_headers / max(len(names), 1), 4),
            "short_fragment_ratio": round(short / max(len(filled), 1), 4),
            "numeric_cell_ratio": round(numerical / max(len(filled), 1), 4),
            "figure_caption_on_page": figure_caption_on_page,
            "nearby_table_caption": nearby_table_caption,
            "figure_caption_below": figure_caption_below,
            "figure_caption_mentions_axis": figure_caption_mentions_axis,
            "table_caption_below": table_caption_below}


def _suspected_graphic(signals: dict[str, Any]) -> bool:
    # Plot ticks and legends can look like numerical or descriptive cells.
    if signals["figure_caption_below"] and not signals["table_caption_below"]:
        if signals["filled_ratio"] <= .17 and not signals["nearby_table_caption"]:
            return True
        if signals["filled_ratio"] <= .035 and signals["figure_caption_mentions_axis"]:
            return True
    # No column-count cutoff: a well-populated 50-column table or a sparse
    # numeric matrix must survive. A caption-labelled sparse categorical table
    # can also be retained. Unlabelled sparse categorical matrices remain an
    # explicit limitation; a reviewed region can disable this negative filter.
    return (signals["filled_ratio"] <= .45
            and signals["nontrivial_header_ratio"] < .25
            and signals["numeric_cell_ratio"] < .35
            and (signals["short_fragment_ratio"] >= .6 or signals["figure_caption_on_page"])
            and not signals["nearby_table_caption"])


def extract_tables(
    page: pymupdf.Page,
    *,
    strategy: str = "lines",
    max_tables: int = 12,
    clip: Sequence[float] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    filter_suspected_figures: bool = True,
) -> list[dict[str, Any]]:
    """Return JSON-safe table Blocks in unrotated CropBox-normalized coordinates.

    ``text`` requires a caller-selected source region; it is never an automatic
    fallback over a full page. Output remains ``verification_status=unverified``.
    Limits bound input/output work but do not constitute a native-engine timeout;
    run this inside the resource-controlled parsing worker.
    """
    if strategy not in {"lines", "lines_strict", "text"}:
        raise ValueError("strategy must be lines, lines_strict, or text")
    if not isinstance(max_tables, int) or isinstance(max_tables, bool) or not 1 <= max_tables <= 50:
        raise ValueError("max_tables must be an integer between 1 and 50")
    if strategy == "text" and clip is None:
        raise TableExtractionError("text_clip_required", "Text table detection requires an explicit source region")
    original_rotation: int | None = None
    try:
        # The installed engine's table finder uses Page.rect when clipping its
        # vector lines. That rectangle is rotated, while drawings/text coordinates
        # are not. Temporarily normalize the in-memory view and always restore it;
        # the source file is never saved or rewritten.
        original_rotation = int(page.rotation)
        if original_rotation:
            page.set_rotation(0)
        width, height = float(page.cropbox.width), float(page.cropbox.height)
        if min(width, height) <= 0 or max(width, height) > MAX_PAGE_EDGE:
            raise TableExtractionError("page_limit", "Page dimensions exceed table extraction limits")
        region = None
        if clip is not None:
            try:
                values = [float(value) for value in clip]
            except (ValueError, TypeError) as exc:
                raise TableExtractionError("invalid_clip", "Clip must contain four finite coordinates") from exc
            if len(values) != 4 or not all(math.isfinite(value) for value in values):
                raise TableExtractionError("invalid_clip", "Clip must contain four finite coordinates")
            region = pymupdf.Rect(values)
            if region.is_empty or region.is_infinite or region.x0 < 0 or region.y0 < 0 or region.x1 > width or region.y1 > height:
                raise TableExtractionError("invalid_clip", "Clip must be inside the unrotated CropBox")
        paths = page.get_drawings()
        path_items = sum(len(path.get("items", ())) for path in paths)
        if len(paths) > MAX_PATHS or path_items > MAX_PATH_ITEMS:
            raise TableExtractionError("path_limit", "Page vector graphics exceed table extraction limits")
        if strategy != "text" and not paths:
            return []
        if strategy == "text" and len(page.get_text(clip=region)) > MAX_TEXT_CHARACTERS:
            raise TableExtractionError("text_limit", "Selected region exceeds table text limits")
        # Explicitly use the documented line/text engine. An optional installed
        # layout model must not silently change the algorithm or download models.
        finder = page.find_tables(strategy=strategy, clip=region, paths=paths, use_layout=False)
        result: list[dict[str, Any]] = []
        for table in finder.tables[:max_tables]:
            row_count, col_count = int(table.row_count), int(table.col_count)
            if row_count < 2 or col_count < 2:
                continue
            if row_count * col_count > MAX_CELLS:
                raise TableExtractionError("cell_limit", "Detected table exceeds cell limits")
            extracted = table.extract()
            rows = [[_cell_text(row[column]) if column < len(row) else None for column in range(col_count)]
                    for row in extracted]
            if len(rows) != row_count:
                raise TableExtractionError("shape_mismatch", "Table row count does not match extracted data")
            if not any(value for row in rows for value in row):
                continue
            header = table.header
            names = [_cell_text(value) for value in (getattr(header, "names", None) or [])]
            signals = _structural_signals(rows, names, page, table.bbox)
            if filter_suspected_figures and _suspected_graphic(signals):
                if diagnostics is not None:
                    diagnostics.append({"page": page.number + 1, "strategy": strategy,
                                        "reason": "sparse_fragmented_graphic",
                                        "source_bbox": _normal_rect(table.bbox, width, height),
                                        "row_count": row_count, "col_count": col_count,
                                        "structural_signals": signals})
                continue
            labels = _header_labels(names, col_count)
            cells: list[dict[str, Any]] = []
            for row_number, row in enumerate(rows):
                source_cells = table.rows[row_number].cells
                for column, value in enumerate(row):
                    source_rect = source_cells[column] if column < len(source_cells) else None
                    cells.append({"row": row_number, "column": column, "text": value,
                                  "bbox": _normal_rect(source_rect, width, height)})
            warnings = ["Cell boundaries, headers, and row alignment are automatically extracted and unverified."]
            if any(value is None for row in rows for value in row):
                warnings.append("Merged or missing cells remain null; values have not been propagated.")
            if any(value and "\n" in value for row in rows for value in row):
                warnings.append("Multiline cells may contain wrapped text or several physical rows; no automatic splitting was applied.")
            if strategy == "text":
                warnings.append("Text-position detection can introduce spurious columns or rows; review the selected region.")
            metadata = {
                "extraction": "pymupdf_find_tables",
                "strategy": strategy,
                "parser_version": pymupdf.VersionBind,
                "verification_status": "unverified",
                "confidence": None,
                "row_count": row_count,
                "col_count": col_count,
                "rows": rows,
                "cells": cells,
                "header": {"names": names, "labels": labels, "external": bool(getattr(header, "external", False)),
                           "bbox": _normal_rect(getattr(header, "bbox", None), width, height),
                           "cells": [_normal_rect(rect, width, height) for rect in (getattr(header, "cells", None) or [])]},
                "source_bbox": [round(float(value), 4) for value in table.bbox],
                "clip": list(values) if clip is not None else None,
                "coordinate_system": "unrotated_cropbox_normalized_top_left",
                "warnings": warnings,
                "is_candidate": True,
                "structural_signals": signals,
                "truncated_tables": len(finder.tables) > max_tables,
            }
            result.append({"type": "table", "text": serialize_table(rows, labels, page_number=page.number + 1),
                           "bbox": _normal_rect(table.bbox, width, height), "metadata": metadata})
        return result
    except TableExtractionError:
        raise
    except Exception as exc:
        raise TableExtractionError("engine_error", f"Table extraction failed ({type(exc).__name__})") from exc
    finally:
        if original_rotation:
            page.set_rotation(original_rotation)
