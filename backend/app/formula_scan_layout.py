"""Find display-math crop suggestions in the stored OCR layout of one page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from .ocr import source_line_spans

_CAPTION = re.compile(r"^(figure|fig\.?|图|table|表)\s*\d", re.I)
_LABEL = re.compile(r"^\(\s*\d{1,3}(?:[.\-]\d{1,3})?[a-z]?\s*\)$", re.I)
_SUFFIX = re.compile(r"\s+(\(\s*\d{1,3}(?:[.\-]\d{1,3})?[a-z]?\s*\))$", re.I)
_LIMIT = re.compile(r"^[a-z]\s*=\s*\d+$", re.I)
_CLAUSE = re.compile(r"\b(?:where|which|that|while)\s+\w+\s+\w+", re.I)
_RELATION = re.compile(r":=|[=≈≃≡≤≥∝≠]")
_LHS = re.compile(r"(?:where\s+)?[\wα-ωΑ-Ω∑∏∫√][\w\d α-ωΑ-Ω∑∏∫√'.,+*/()\[\]{}_|^−\-]{0,46}$", re.I)
_DISPLAY_SYMBOL = re.compile(r"[∑∏∫√]")
_WORDS = re.compile(r"[a-zA-Z]{3,}")
_HAN = re.compile(r"[\u3400-\u9fff]")
_PROGRAM = re.compile(r"\b(?:def\s+\w+\(|(?:for|while)\b.+:|raise\s+\w+\(|"
                      r"\w+\.\w+\(|(?:True|False|None)\s*\]|\[\]\s+\w+\s*=)")


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


def _math(line, max_width=.62):
    text = line.text
    if (not 3 <= len(text) <= 125 or not .035 <= line.width <= max_width
            or "@" in text or "://" in text or len(_HAN.findall(text)) >= 5 or _PROGRAM.search(text)):
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


def _gutter(prose, captions):
    # Estimate the inner page margins from the long prose lines. Short headings
    # and columns in a single figure cannot establish a page-wide gutter.
    left = [row for row in prose if row.width >= .24 and row.bbox[2] <= .516]
    right = [row for row in prose if row.width >= .24 and row.bbox[0] >= .484]
    if len(left) >= 5 and len(right) >= 5:
        starts = sorted(row.bbox[0] for row in right)
        ends = sorted(row.bbox[2] for row in left)
        inner_left = ends[(len(ends) - 1) * 3 // 4]
        inner_right = starts[(len(starts) - 1) // 4]
        return (inner_left + inner_right) / 2 if inner_right - inner_left >= .012 else None

    if not any(row.bbox[0] < .35 and row.bbox[2] > .65 for row in prose):
        return None
    # A narrow body column can be paired with a side figure and caption.
    for caption in captions:
        if caption.width < .20:
            continue
        if caption.bbox[0] >= .48:
            edge = caption.bbox[0]
            neighbors = [r.bbox[2] for r in left if edge - .10 < r.bbox[2] <= edge - .012
                         and caption.bbox[1]-.17 <= r.center_y <= caption.bbox[3]+.035]
        elif caption.bbox[2] <= .52:
            edge = caption.bbox[2]
            neighbors = [r.bbox[0] for r in right if edge + .012 <= r.bbox[0] < edge + .10
                         and caption.bbox[1]-.17 <= r.center_y <= caption.bbox[3]+.035]
        else:
            continue
        if len(neighbors) >= 5:
            return (edge + median(neighbors)) / 2
    return None


def _column_regions(rows, prose, blocked, bounds, height, roots=None):
    left_edge = median(row.bbox[0] for row in prose) if prose else bounds[0]
    right_edges = sorted(row.bbox[2] for row in prose)
    right_edge = right_edges[(len(right_edges) - 1) * 3 // 4] if right_edges else bounds[1]
    max_width = .84 if roots is not None else .62
    labels = [row for row in rows if _LABEL.fullmatch(row.text) and row.bbox[0] > right_edge - .105]
    algebra = [row for row in rows if (roots is None or row in roots) and _RELATION.search(row.text)
               and row.width >= .055 and not _LIMIT.fullmatch(row.text) and not _PROGRAM.search(row.text)]
    scoped = [row for row in algebra if not any(
        a <= (row.bbox[0]+row.bbox[2])/2 <= c and b <= row.center_y <= d for a,b,c,d in blocked
    )]
    numbers = {}
    for label in labels:
        candidates = [row for row in scoped if row not in numbers and row.bbox[2] <= label.bbox[0] + .004
                      and row.bbox[0] <= label.bbox[0] - .069
                      and abs(row.center_y - label.center_y) <= height * .62
                      and len(_WORDS.findall(row.text)) <= 7]
        if not candidates:
            # The number beside a stacked fraction may sit below the baseline.
            candidates = [row for row in scoped if row not in numbers and row.bbox[2] < label.bbox[0]
                          and _RELATION.fullmatch(row.text[-1:]) and height*.62 < label.center_y-row.center_y < height*2.6]
        if candidates:
            row = min(candidates, key=lambda item: (abs(item.center_y-label.center_y), -item.width))
            numbers[row] = label

    anchors = {}
    for row in scoped:
        suffix = _SUFFIX.search(row.text) if row.bbox[2] >= right_edge - .054 and _math(row, max_width) else None
        if row in numbers or suffix:
            anchors[row] = (numbers.get(row), suffix)
            continue
        if not _math(row, max_width) or any(abs(root.center_y-row.center_y) < height*.75 for root in numbers):
            continue
        before = min((row.bbox[1] - line.bbox[3] for line in prose if line.center_y < row.center_y), default=1)
        after = min((line.bbox[1] - row.bbox[3] for line in prose if line.center_y >= row.center_y), default=1)
        detached_clause = any(line is not row and abs(line.center_y-row.center_y) < height*.45 and
                              (len(_WORDS.findall(line.text)) >= 3 or _CLAUSE.search(line.text)) and not _math(line)
                              for line in rows)
        if before >= height*.16 and after >= height*.13 and not detached_clause:
            if row.bbox[0] >= left_edge + .019 or "(" in row.text.split("=", 1)[0]:
                anchors[row] = (None, None)

    extras = {row: [] for row in anchors}
    for line in rows:
        if line in anchors or line in prose or _LABEL.fullmatch(line.text) or len(_WORDS.findall(line.text)) >= 3:
            continue
        nearby = []
        for root, (number, suffix) in anchors.items():
            stacked = bool(_RELATION.fullmatch(root.text[-1:]))
            right = number.bbox[0] if number else bounds[1]
            if (abs(root.center_y-line.center_y) <= height*(1.75 if stacked else .75)
                    and (line.bbox[0] >= root.bbox[0] - .019 if stacked else
                         _overlap(line.bbox[0], line.bbox[2], root.bbox[0], root.bbox[2]) >= -.016)
                    and line.bbox[2] <= right + .035):
                nearby.append(root)
        if nearby:
            root = min(nearby, key=lambda item: (abs(item.center_y-line.center_y), item.bbox))
            extras[root].append(line)

    regions = []
    for row, (label, suffix) in sorted(anchors.items(), key=lambda entry: entry[0].center_y):
        parts = [row, *extras[row]]
        x0, y0 = (min(line.bbox[i] for line in parts) for i in (0, 1))
        x1, y1 = (max(line.bbox[i] for line in parts) for i in (2, 3))
        above = max((line.bbox[3] for line in prose if line.bbox[3] <= row.bbox[1]), default=.052)
        top = min(y0, above + .006)
        bottom = min((line.bbox[1] for line in prose if line.bbox[1] >= row.bbox[3]), default=.932)
        bottom = min(bottom, min((line.bbox[1] for line in rows if line not in anchors
                                  and line.text.casefold().startswith("where ")
                                  and line.bbox[1] > row.center_y + height*.65), default=.932))
        for other in anchors:
            if other.center_y < row.center_y:
                top = max(top, (other.bbox[3] + row.bbox[1]) / 2)
            elif other.center_y > row.center_y:
                bottom = min(bottom, (other.bbox[1] + row.bbox[3]) / 2)
        detached = label is not None and label.center_y - row.center_y > height*.62
        if label is None and suffix is None and row.text.count("(") > row.text.count(")"):
            # Unclosed OCR fragments can hide the end of the equation. Use the
            # remaining width of this column while keeping the prose row limits.
            x1 = max(x1, right_edge)
        right = min(x1+.008, label.bbox[0]-.003) if label and not detached else x1+.008
        if detached:
            bottom = min(bottom, label.bbox[3])
        bbox = (max(bounds[0], x0-.008), max(top, y0-.008), min(bounds[1], right), min(bottom, y1+.006))
        if bbox[2]-bbox[0] >= .028 and .009 <= bbox[3]-bbox[1] <= .105:
            hint = row.text[:suffix.start()] if suffix else row.text
            regions.append({
                "bbox": [round(v, 6) for v in bbox], "hint": hint[:108] if _math(row, max_width) else "公式原图",
                "label": label.text if label else suffix[1] if suffix else None,
                **({"label_trim": "bottom"} if detached else {"label_trim": "right"} if suffix else {}),
            })
    return regions


def _inside_column(line, bounds):
    # Allow rounding at the gutter, then clip the saved crop to its own side.
    return bounds[0] - .008 <= line.bbox[0] and line.bbox[2] <= bounds[1] + .008


def _spanning_sections(rows, captions, blocked, prose, gutter, height):
    wide_prose = [row for row in prose if row.bbox[0] < gutter-.12 and row.bbox[2] > gutter+.12]
    side_prose = [row for row in prose if _inside_column(row, (0, gutter)) or _inside_column(row, (gutter, 1))]
    right_edge = (median(row.bbox[2] for row in wide_prose) if wide_prose else
                  max(row.bbox[2] for row in side_prose))
    labels = [row for row in rows if _LABEL.fullmatch(row.text) and row.bbox[0] > right_edge-.105]
    wide_captions = [row for row in captions if row.bbox[0] < gutter-.12 and row.bbox[2] > gutter+.12]
    blocked = blocked + _caption_bands(wide_captions, prose)

    seeds = []
    for row in rows:
        x0, y0, x1, y1 = row.bbox
        if (not _RELATION.search(row.text) or not .055 <= row.width <= .84 or _LIMIT.fullmatch(row.text)
                or _PROGRAM.search(row.text)):
            continue
        center = (x0+x1)/2
        if any(a <= center <= c and b <= row.center_y <= d for a,b,c,d in blocked):
            continue
        if any(abs(other.center_y-row.center_y) <= height*.7 for other in side_prose):
            continue
        spans_gutter = x0 < gutter-.023 and x1 > gutter+.023
        in_wide_context = any(abs(other.center_y-row.center_y) <= .19 for other in wide_prose)
        wide_number = x0 < gutter-.020 and center >= gutter-.125 and any(
            label.bbox[0]-x1 > .085 and abs(label.center_y-row.center_y) <= height*.72 for label in labels
        )
        if wide_number and not spans_gutter:
            # The outer number may belong to a simultaneous right-column line.
            wide_number = not any(
                other.bbox[0] >= gutter and _RELATION.search(other.text)
                and other.width >= .055 and abs(other.center_y-row.center_y) <= height*.75
                for other in rows
            )
        if (spans_gutter and _math(row, .84)) or (in_wide_context and wide_number):
            seeds.append(row)

    # Fold nearby spanning lines together, preserving intervening column text.
    sections = []
    for row in sorted(seeds, key=lambda item: item.center_y):
        if (sections and row.bbox[1]-sections[-1][-1].bbox[3] < height*2.4
                and not any(sections[-1][-1].center_y < line.center_y < row.center_y for line in side_prose)):
            sections[-1].append(row)
        else:
            sections.append([row])
    found = []
    for roots in sections:
        first, last = roots[0], roots[-1]
        top = max((row.bbox[3] for row in side_prose if row.bbox[3] <= first.bbox[1]), default=0)
        bottom = min((row.bbox[1] for row in side_prose if row.bbox[1] >= last.bbox[3]), default=1)
        top = max(top, first.bbox[1]-height*1.8)
        bottom = min(bottom, last.bbox[3]+height*1.8)
        band = [line for line in rows if top <= line.center_y <= bottom]
        nearby_prose = [line for line in wide_prose if top-.12 <= line.center_y <= bottom+.12]
        if not nearby_prose:
            nearby_prose = wide_prose
        regions = _column_regions(band, nearby_prose, blocked, (0, 1), height, set(roots))
        if regions:
            found.append((top, bottom, regions))
    return found


def _two_columns(rows, captions, blocked, prose, gutter, height):
    spanning = _spanning_sections(rows, captions, blocked, prose, gutter, height)
    rows = [row for row in rows if not any(top <= row.center_y <= bottom for top, bottom, _ in spanning)]
    regions = []
    for side, bounds in enumerate(((0, gutter), (gutter, 1))):
        column = [row for row in rows if _inside_column(row, bounds)]
        context = [row for row in prose if _inside_column(row, bounds)]
        column_captions = [c for c in captions if _inside_column(c, bounds) or
                           (c.bbox[0] < gutter - .12 and c.bbox[2] > gutter + .12)]
        if context:
            column_regions = _column_regions(column, context, blocked + _caption_bands(column_captions, context), bounds, height)
            if spanning:
                for item in column_regions:
                    center = (item["bbox"][1] + item["bbox"][3]) / 2
                    band = sum(bottom < center for _, bottom, _ in spanning)
                    item["split_group"] = f"column-{side}-{band}"
            regions.extend(column_regions)
    # Preserve left/right reading order inside each band of columns.
    ordered = []
    for index, (top, _, wide) in enumerate(spanning):
        for item in wide:
            item["split_group"] = f"wide-{index}"
        before = [item for item in regions if (item["bbox"][1]+item["bbox"][3])/2 < top]
        ordered.extend(before)
        ordered.extend(wide)
        regions = [item for item in regions if item not in before]
    return (ordered+regions)[:24]


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
    gutter = _gutter(prose, captions)
    if gutter is not None:
        return _two_columns(rows, captions, blocked, prose, gutter, height)
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
