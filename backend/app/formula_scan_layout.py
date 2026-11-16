"""Find display-math crop suggestions in the stored OCR layout of one page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from .ocr import source_line_spans

_CAPTION = re.compile(r"^(figure|fig\.?|图|table|表)\s*\d", re.I)
_LABEL = re.compile(r"^\(\s*\d{1,3}(?:[.\-]\d{1,3})?[a-z]?\s*\)$", re.I)
_RELATION = re.compile(r"[=≈≃≡≤≥∝≠]")
_LHS = re.compile(r"(?:where\s+)?[\wα-ωΑ-Ω∑∏∫√][\w\d α-ωΑ-Ω∑∏∫√'.,+*/()\[\]{}_|^−\-]{0,46}$", re.I)
_DISPLAY_SYMBOL = re.compile(r"[∑∏∫√]")
_WORDS = re.compile(r"[a-zA-Z]{3,}")
_HAN = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class Line:
    text: str
    bbox: tuple[float, float, float, float]

    @property
    def center_y(self):
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def width(self):
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self):
        return self.bbox[3] - self.bbox[1]


def _math(line):
    text = line.text
    if (not 3 <= len(text) <= 125 or not .035 <= line.width <= .62
            or "@" in text or "://" in text or len(_HAN.findall(text)) >= 5):
        return False
    relation = _RELATION.search(text)
    if relation:
        left = text[:relation.start()].strip()
        right = text[relation.end():].strip()
        # A clause like "We use x = 2" has prose on at least one side.
        return bool(_LHS.fullmatch(left) and
                    (len(left.removeprefix("where ").split()) <= 2 or "(" in left) and
                    not re.search(r"\b[a-zA-Z]{3,}(?:\s+[a-zA-Z]{3,}){2,}\b", right))
    return bool(_DISPLAY_SYMBOL.match(text) and len(_WORDS.findall(text)) <= 3)


def _overlap(left, right, other_left, other_right):
    return min(right, other_right) - max(left, other_left)


def _prose(line):
    return line.width >= .23 and (len(_WORDS.findall(line.text)) >= 6 or len(_HAN.findall(line.text)) >= 14)


def _caption_bands(captions, prose):
    bands = []
    for caption in captions:
        x0, y0, x1, y1 = caption.bbox
        # A short centered caption can still describe a full-width illustration.
        left, right = (0.06, .94) if .38 < (x0 + x1) / 2 < .62 else (x0 - .035, x1 + .035)
        aligned = [row for row in prose if _overlap(left, right, row.bbox[0], row.bbox[2]) > .08]
        if re.match(r"^(table|表)", caption.text, re.I):
            boundary = min((row.bbox[1] for row in aligned if row.bbox[1] > y1 + .012), default=.94)
            bands.append((left, y0, right, boundary))
        else:
            boundary = max((row.bbox[3] for row in aligned if row.bbox[3] < y0 - .012), default=.055)
            bands.append((left, boundary, right, y1))
    return bands


def locate(blocks):
    """Return independent same-page regions, leaving uncertain text in the reader."""
    rows = []
    captions = []
    blocked = []
    for block in blocks:
        if block["type"] == "table" or (block["type"] == "figure" and
                block["bbox"][2] - block["bbox"][0] < .85 and block["bbox"][3] - block["bbox"][1] < .85):
            blocked.append(block["bbox"])
        if block["type"] in {"heading", "title"}:
            continue
        if _CAPTION.match(block["text"]):
            captions.append(Line(block["text"], tuple(block["bbox"])))
            continue
        for span in source_line_spans(block):
            row = Line(span["text"].strip(), tuple(span["bbox"]))
            if .055 < row.center_y < .93 and row.height <= .056 and row.width >= row.height * .65:
                rows.append(row)
    if not rows:
        return []
    height = max(.008, min(.023, median(row.height for row in rows)))
    prose = [row for row in rows if _prose(row) and not _math(row)]
    blocked += _caption_bands(captions, prose)
    labels = [row for row in rows if _LABEL.fullmatch(row.text)]
    candidates = []
    for row in rows:
        x0, y0, x1, y1 = row.bbox
        if not _math(row) or any(a <= (x0+x1)/2 <= c and b <= row.center_y <= d for a,b,c,d in blocked):
            continue
        neighbors = [line for line in prose if abs(line.center_y - row.center_y) < .15
                     and _overlap(x0,x1,line.bbox[0],line.bbox[2]) >= min(.052, row.width*.4)]
        if not neighbors:
            continue
        margin = median(line.bbox[0] for line in neighbors)
        right_edge = median(line.bbox[2] for line in neighbors)
        number = next((line for line in labels if .03 < line.bbox[0] - x1 and
                       right_edge - .11 <= line.bbox[2] <= right_edge + .06
                       and abs(line.center_y - row.center_y) <= height*.75), None)
        if x0 < margin + .027 and not number:
            continue
        if any(y0 < line.center_y < y1 for line in neighbors):
            continue
        candidates.append((row, number, neighbors))

    equations = [row for row, _, _ in candidates if _RELATION.search(row.text)]
    fragments = {
        row for row, _, _ in candidates
        if row not in equations and row.width <= .13 and any(
            abs(base.center_y - row.center_y) <= height*.95
            and _overlap(row.bbox[0], row.bbox[2], base.bbox[0], base.bbox[2]) >= -.015
            for base in equations
        )
    }
    candidates = [item for item in candidates if item[0] not in fragments]
    anchors = {row for row, _, _ in candidates}
    extras = {row: [] for row in anchors}
    for line in rows:
        if line in anchors or line in prose or line in labels or (_math(line) and line not in fragments):
            continue
        if not (len(line.text) <= 22 or (len(line.text) <= 90 and re.search(r"[0-9√·−+^]", line.text))):
            continue
        x0, _, x1, _ = line.bbox
        nearby = [root for root in anchors if abs(root.center_y - line.center_y) < height*1.4
                  and _overlap(x0,x1,root.bbox[0],root.bbox[2]) >= -.045]
        if nearby:
            closest = min(nearby, key=lambda root: (abs(root.center_y-line.center_y), root.bbox))
            extras[closest].append(line)

    regions = []
    for row, number, neighbors in sorted(candidates, key=lambda item: (item[0].bbox[1], item[0].bbox[0])):
        parts = [row, *extras[row]]
        x0, y0 = (min(part.bbox[i] for part in parts) for i in (0, 1))
        x1, y1 = (max(part.bbox[i] for part in parts) for i in (2, 3))
        top = max((line.bbox[3] for line in neighbors if line.bbox[3] <= y0), default=.052)
        bottom = min((line.bbox[1] for line in neighbors if line.bbox[1] >= y1), default=.932)
        for other in anchors - {row}:
            if _overlap(x0, x1, other.bbox[0], other.bbox[2]) < .03:
                continue
            if other.center_y < row.center_y and other.bbox[3] <= y0 + .004:
                top = max(top, (other.bbox[3] + y0) / 2)
            if other.center_y > row.center_y and other.bbox[1] >= y1 - .004:
                bottom = min(bottom, (other.bbox[1] + y1) / 2)
        right = min(x1 + .011, number.bbox[0] - .005) if number else x1 + .011
        bbox = (max(0, x0 - .009), max(top, y0 - .010), min(1, right), min(bottom, y1 + .006))
        if .028 <= bbox[2] - bbox[0] <= .73 and .009 <= bbox[3] - bbox[1] <= .105:
            regions.append({"bbox": [round(value, 6) for value in bbox],
                            "hint": row.text[:108], "label": number.text if number else None})
    return regions[:24]
