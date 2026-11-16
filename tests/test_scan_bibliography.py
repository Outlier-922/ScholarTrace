from backend.app import parser, reading


def scan(identity, page, *lines, kind="paragraph", level=None):
    boxes = [box for _, box in lines]
    return {
        "id": identity, "page": page, "text": "\n".join(text for text, _ in lines),
        "type": kind, "heading_level": level, "section_path": [],
        "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                 max(b[2] for b in boxes), max(b[3] for b in boxes)],
        "metadata": {"extraction": "rapidocr", "lines": [{"text": text, "bbox": bbox} for text, bbox in lines]},
    }


def test_references_restore_split_line_in_printed_order_and_keep_exact_fragments():
    blocks = [
        scan("references", 10, ("References", [.17, .31, .24, .32]), kind="heading", level=1),
        scan("a", 10, ("[1] An author. Some paper. arXiv preprint", [.18, .367, .82, .383])),
        scan("year", 10, ("2016.", [.328, .379, .377, .392])),
        scan("identifier", 10, ("arXiv:1607.06450", [.209, .380, .337, .395])),
        scan("b", 10, ("[2] Another author. Another paper.", [.18, .404, .82, .419])),
    ]
    a, b = parser._references(blocks)
    assert a["marker"] == "1" and a["arxiv_id"] == "1607.06450"
    assert a["text"].endswith("\narXiv:1607.06450 2016.")
    assert [part["block_id"] for part in a["source_spans"]] == ["a", "identifier", "year"]
    assert b["marker"] == "2"


def test_reference_continuation_across_pages_skips_folios_and_stops_at_appendix(monkeypatch):
    printed = "[7] Author. A title which continues on the next page"
    blocks = [
        scan("body", 1, ("See also the prior analysis [7].", [.14, .2, .40, .216]),
             ("An unrelated second line.", [.14, .216, .40, .232])),
        scan("heading", 2, ("References", [.14, .74, .24, .755]), kind="heading", level=1),
        scan("a", 2, (printed, [.14, .87, .78, .888])),
        scan("folio", 2, ("2", [.49, .93, .51, .944])),
        scan("continued", 3, ("before the publication venue is printed, 2019.", [.19, .09, .68, .105])),
        scan("b", 3, ("[8] Another author. Another publication, 2021.", [.14, .14, .75, .155])),
        scan("appendix", 4, ("Appendix B: Additional Results", [.14, .10, .34, .114]), kind="heading", level=1),
        scan("newbody", 4, ("The supplementary data extend [8].", [.14, .13, .75, .145])),
    ]
    entries = parser._references(blocks)
    assert [entry["marker"] for entry in entries] == ["7", "8"]
    first = entries[0]
    assert first["text"] == printed + "\nbefore the publication venue is printed, 2019."
    assert [(part["page"], part["block_id"]) for part in first["source_spans"]] == [(2, "a"), (3, "continued")]
    lookup = {item["id"]: item for item in blocks}
    for entry in entries:
        for span in entry["source_spans"]:
            assert lookup[span["block_id"]]["text"][span["start"]:span["end"]] == span["text"]
    structure = {"parse_run_id": "run", "blocks": blocks, "references": [
        {**item, "id": f"ref-{item['marker']}"} for item in entries]}
    monkeypatch.setattr(reading.store, "get_structure", lambda *args, **kwargs: structure)
    mapping = reading.citation_map("doc")
    assert [(item["page"], item["marker_text"]) for item in mapping["links"]] == [(1, "[7]"), (4, "[8]")]
    link = mapping["links"][0]
    assert link["line_bbox"] == [.14, .20, .40, .216] and link["reference_ids"] == ["ref-7"]
    assert mapping["references"][0]["backlinks"] == [link]


def test_two_columns_keep_separate_reference_runs_in_reading_order():
    blocks = [
        scan("heading", 2, ("References", [.10, .10, .20, .114]), kind="heading", level=1),
        scan("b", 2, ("[8] Second column reference.", [.55, .14, .9, .154])),
        scan("a", 2, ("[7] First column reference.", [.10, .14, .47, .154])),
        scan("continuation_b", 2, ("Journal for entry eight, 2021.", [.59, .154, .93, .168])),
        scan("continuation_a", 2, ("Journal for entry seven, 2019.", [.14, .154, .47, .168])),
    ]
    a, b = parser._references(blocks)
    assert a["marker"] == "7" and "entry seven" in a["text"] and "entry eight" not in a["text"]
    assert b["marker"] == "8" and "entry eight" in b["text"] and "entry seven" not in b["text"]
