from backend.app.formula_continuation import suggest


def item(key, text, box, kind="paragraph"):
    return {"id": key, "type": kind, "text": text, "bbox": box}


def test_page_turn_suggests_two_source_crops_only_for_an_unfinished_equality():
    first = [
        item("operand", "∂L", [.72, .87, .81, .9]),
        item("pending", "p |2=", [.80, .89, .91, .911]),
        item("footer", "12", [.49, .963, .52, .977]),
    ]
    second = [
        item("running", "A running title", [.25, .061, .6, .07], "heading"),
        item("open", "O(∥u", [.09, .085, .15, .099]),
        item("norm", "∥2", [.17, .085, .23, .104]),
        item("relation", "≤ v)", [.24, .09, .31, .104]),
        item("next", "Separate numbered equation", [.12, .145, .47, .164]),
    ]
    candidate = suggest(first, second)
    assert candidate["source_block_ids"] == ["operand", "pending", "open", "norm", "relation"]
    assert candidate["region"][3] < .92 and candidate["next_region"][3] < .12
    assert suggest([*first, item("after", "Another paragraph", [.51, .921, .89, .936])], second) is None
    assert suggest([first[0], {**first[1], "text": "p |2= 1 (14)"}], second) is None
    assert suggest(first, [item("heading", "New section", [.09, .077, .42, .082], "heading"), *second]) is None
