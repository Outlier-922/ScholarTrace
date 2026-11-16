"""Group numeric scan references by printed markers and original line positions."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any

from .ocr import source_line_spans

_MARKER = re.compile(r"^\[\s*(\d{1,4})\s*\]\s*")
_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
_YEAR_END = re.compile(r"(?:19|20)\d{2}[.)\s]*$")


@dataclass
class Row:
    sources: list[dict[str, Any]]

    @property
    def bbox(self) -> list[float]:
        bounds = [part["bbox"] for part in self.sources]
        return [min(b[0] for b in bounds), min(b[1] for b in bounds),
                max(b[2] for b in bounds), max(b[3] for b in bounds)]

    @property
    def text(self) -> str:
        return " ".join(part["text"] for part in self.sources)

    @property
    def page(self) -> int:
        return self.sources[0]["page"]


def _column_rows(spans: list[dict[str, Any]]) -> list[Row]:
    if not spans:
        return []
    height = median(part["bbox"][3] - part["bbox"][1] for part in spans)
    rows: list[Row] = []
    for part in sorted(spans, key=lambda p: (p["bbox"][1], p["bbox"][0])):
        left, top, right, bottom = part["bbox"]
        target = None
        for row in reversed(rows):
            x0, y0, x1, y1 = row.bbox
            if top - y1 > height:
                break
            overlap = min(bottom, y1) - max(top, y0)
            box_width = min(right - left, x1 - x0)
            separated = -min(.016, box_width * .28) <= max(left - x1, x0 - right) <= .10
            if overlap >= .64 * min(bottom - top, y1 - y0) and separated:
                target = row
                break
        if target is None:
            rows.append(Row([part]))
        else:
            target.sources.append(part)
            target.sources.sort(key=lambda p: p["bbox"][0])
    return rows


def _rows(spans: list[dict[str, Any]]) -> list[Row]:
    # Multiple reference starts well apart on x indicate independent columns.
    markers = sorted(part["bbox"][0] for part in spans if _MARKER.match(part["text"]))
    splits = [(left + right) / 2 for left, right in zip(markers, markers[1:]) if right - left > .28]
    if not splits:
        return _column_rows(spans)
    split = max(splits)
    edge = min(x for x in markers if x >= split)
    # The gutter sits just before the first right-column label, not halfway
    # between starts. Hanging indents in the left column may exceed that midpoint.
    gutter = edge - .025
    return (_column_rows([part for part in spans if part["bbox"][0] < gutter])
            + _column_rows([part for part in spans if part["bbox"][0] >= gutter]))


def _continues(start: Row, previous: Row, current: Row) -> bool:
    left, top, _, bottom = current.bbox
    _, old_top, _, old_bottom = previous.bbox
    indent = left - start.bbox[0]
    if not -.008 <= indent <= .18:
        return False
    if current.page == previous.page:
        return -.006 <= top - old_bottom <= max(bottom - top, old_bottom - old_top) * 1.4
    return (current.page == previous.page + 1 and old_bottom >= .76 and .055 <= top <= .23
            and indent >= .012 and not _YEAR_END.search(previous.text))


def numbered_scan_entries(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_page = defaultdict(list)
    for block in blocks:
        for span in source_line_spans(block):
            x0, y0, x1, y1 = span["bbox"]
            folio = _PAGE_NUMBER.fullmatch(span["text"]) and (y0 > .90 or y1 < .10) and .42 < (x0 + x1) / 2 < .58
            if y0 < .94 and x1 - x0 >= (y1 - y0) * .7 and not folio:
                by_page[span["page"]].append(span)

    entries: list[tuple[str, list[Row]]] = []
    for page in sorted(by_page):
        for row in _rows(by_page[page]):
            marker = _MARKER.match(row.text)
            if marker and len(row.text[marker.end():].strip()) >= 3:
                entries.append((str(int(marker[1])), [row]))
            elif entries:
                rows = entries[-1][1]
                if _continues(rows[0], rows[-1], row):
                    rows.append(row)

    result = []
    for marker, rows in entries:
        sources = [part for row in rows for part in row.sources]
        result.append({"text": "\n".join(row.text for row in rows), "marker": marker,
                       "page": sources[0]["page"], "block_id": sources[0]["block_id"], "source_spans": sources})
    return result
