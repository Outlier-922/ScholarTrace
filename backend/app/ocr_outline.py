"""Identify scanned section titles before OCR lines are grouped into paragraphs."""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any

HEADING_WORDS = re.compile(r"^(abstract|introduction|background|related work|method(?:s|ology)?|"
                           r"experiments?|results?|discussion|conclusions?|references|bibliography|"
                           r"appendix(?:\s+[A-Z])?|acknowledg(?:e)?ments?|摘要|引言|参考文献|附录)\s*$", re.I)
_ABSTRACT = re.compile(r"^(abstract|摘要)$", re.I)
_NUMBER = r"[1-9]\d?(?:\.(?:0|[1-9]\d?)){0,3}"
_NUMBER_ONLY = re.compile(rf"^{_NUMBER}\.?$")
_NUMBERED = re.compile(rf"^({_NUMBER})\.?\s+(.+)$")
_APPENDIX = re.compile(r"^(?:Appendix\b|附录)", re.I)
_CAPTION = re.compile(r"^(?:figure|fig\.|table|图|表)\s*\d", re.I)


def is_horizontal(line: dict[str, Any]) -> bool:
    left, top, right, bottom = line["rect"]
    return right - left >= (bottom - top) * 1.2


def _title_text(text: str) -> bool:
    return (3 <= len(text) <= 110 and len(text.split()) <= 14
            and (text[0].isupper() or "\u4e00" <= text[0] <= "\u9fff")
            and not text.endswith((".", ",", ";", "="))
            and not any(char in text for char in "@=[]<>%"))


def annotate_headings(lines: list[dict[str, Any]], width: float, height: float,
                      page_number: int) -> list[dict[str, Any]]:
    """Return OCR lines with section levels, joining a detached section number to its title."""
    horizontal = [line for line in lines if is_horizontal(line)]
    broad = [line for line in horizontal if line["rect"][2] - line["rect"][0] >= width * .22
             and len(line["text"]) >= 30 and not _CAPTION.match(line["text"])]
    body_size = statistics.median(line["font_size"] for line in (broad or horizontal)) if horizontal else 10
    margin_step = max(1, width * .015)
    edges = Counter(round(line["rect"][0] / margin_step) for line in broad)
    body_edges = {position for position, count in edges.items() if count >= 2}
    if not body_edges and edges:
        body_edges.add(edges.most_common(1)[0][0])
    abstract_top = min((line["rect"][1] for line in horizontal if _ABSTRACT.fullmatch(line["text"])), default=0)
    captions = [line for line in horizontal if _CAPTION.match(line["text"])]
    row_step = max(2, body_size)

    def row(line: dict[str, Any]) -> int:
        return int((line["rect"][1] + line["rect"][3]) / (2 * row_step))

    rows = defaultdict(list)
    for index, line in enumerate(lines):
        if is_horizontal(line):
            rows[row(line)].append(index)

    def at_body_edge(left: float) -> bool:
        return not body_edges or any(abs(left - position * margin_step) <= 1.4 * margin_step for position in body_edges)

    detached: dict[int, int] = {}
    used: set[int] = set()
    for index, line in enumerate(lines):
        left, top, right, bottom = line["rect"]
        if (not _NUMBER_ONLY.fullmatch(line["text"]) or not at_body_edge(left)
                or top >= height * .93 or page_number == 1 and top < abstract_top):
            continue
        candidates = []
        lane = row(line)
        for neighbor in (lane - 1, lane, lane + 1):
            for other in rows[neighbor]:
                x0, y0, _, y1 = lines[other]["rect"]
                if (x0 > left and -.3 * body_size <= x0 - right <= 1.8 * body_size
                        and min(bottom, y1) - max(top, y0) >= .65 * min(bottom - top, y1 - y0)):
                    candidates.append(other)
        if len(candidates) == 1:
            other = candidates[0]
            if other not in used and _title_text(lines[other]["text"]):
                detached[index] = other
                used.add(other)

    result = []
    for index, line in enumerate(lines):
        if index in used:
            continue
        parts = [line, lines[detached[index]]] if index in detached else [line]
        text = " ".join(part["text"] for part in parts)
        left, top, right, bottom = line["rect"]
        level = None
        if (len(parts) == 2 or is_horizontal(line)) and top < height * .93 and not _CAPTION.match(text):
            number = _NUMBERED.fullmatch(text)
            if HEADING_WORDS.fullmatch(text):
                level = 1
            elif page_number != 1 or top >= abstract_top:
                if number and _title_text(number[2]) and at_body_edge(left):
                    level = min(3, number[1].count(".") + 1)
                elif _APPENDIX.match(text) and _title_text(text) and at_body_edge(left):
                    level = 1
                elif (page_number > 1 and top < height * .17 and right - left < width * .48
                      and len(text.split()) >= 2 and _title_text(text)
                      and any(abs(left - caption["rect"][0]) < margin_step
                              and bottom < caption["rect"][1]
                              and line["font_size"] >= caption["font_size"] * 1.12 for caption in captions)
                      and not any(at_body_edge(other["rect"][0]) and other["rect"][1] < top
                                  for other in horizontal)):
                    # An isolated title at the top of a plate can introduce figures after the bibliography.
                    level = 1
        if len(parts) == 2:
            a, b = (part["rect"] for part in parts)
            line = {**line, "text": text, "rect": [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])],
                    "font_size": max(part["font_size"] for part in parts),
                    "confidence": statistics.mean(part["confidence"] for part in parts), "source_lines": parts}
        result.append({**line, "body_size": body_size, "ocr_heading_level": level})
    return result
