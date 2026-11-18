"""A source-position hint for an unfinished equation at a page turn."""

import re

_ENDING = re.compile(r"(?:\|\s*\d+|[)\]])\s*=\s*$")
_MATH = re.compile(r"[∂∇∥∑∏√≤≥]")
_NUMBER = re.compile(r"\(\s*\d+[a-z]?\s*\)\s*$", re.I)
_EXCLUDED = {"reference", "reference_entry", "figure", "figure_caption", "table", "table_caption"}


def _bounds(blocks, left_pad=.004):
    boxes = [item["bbox"] for item in blocks]
    return [max(0, min(box[0] for box in boxes) - left_pad),
            max(0, min(box[1] for box in boxes) - .004),
            min(1, max(box[2] for box in boxes) + .004),
            min(1, max(box[3] for box in boxes) + .004)]


def suggest(current, following):
    """Return editable crops only when the page ends in a dangling equality."""
    last = [item for item in current if item["type"] not in _EXCLUDED
            and item["bbox"][0] >= .48 and .84 <= item["bbox"][1] <= .925
            and item["bbox"][3] <= .95]
    endings = [item for item in last if _ENDING.search(item["text"])]
    if len(endings) != 1:
        return None
    ending = endings[0]
    x, top, _, bottom = ending["bbox"]
    if any(item["bbox"][1] > bottom + .004 for item in last):
        return None
    tail = [item for item in last if item["bbox"][0] >= x - .08 - 1e-6
            and item["bbox"][1] < bottom and item["bbox"][3] > top - .012]
    if not any(_MATH.search(item["text"]) for item in tail) or any(_NUMBER.search(item["text"]) for item in tail):
        return None

    leading = sorted(
        (item for item in following if item["type"] not in _EXCLUDED
         and item["bbox"][0] < .44 and .074 <= item["bbox"][1] <= .2),
        key=lambda item: item["bbox"][1],
    )
    if not leading or leading[0]["type"] in {"title", "heading"} or leading[0]["bbox"][1] > .145:
        return None
    start = leading[0]["bbox"][1]
    head = []
    edge = start
    for item in leading:
        box = item["bbox"]
        if box[1] > edge + .006 or box[3] - start > .055 or item["type"] in {"title", "heading"}:
            break
        head.append(item)
        edge = max(edge, box[3])
    if len(head) < 3 or sum(bool(_MATH.search(item["text"])) for item in head) < 2:
        return None
    return {"region": _bounds(tail, left_pad=.012), "next_region": _bounds(head),
            "source_block_ids": [item["id"] for item in tail + head]}
