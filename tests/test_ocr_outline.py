from backend.app.parser import _ocr_paragraphs, _references


def line(text, bbox, size=10):
    return {"text": text, "rect": bbox, "font_size": size, "confidence": .94, "bold": False}


def test_scanned_headings_keep_number_and_title_boxes_and_ignore_front_matter():
    lines = [
        line("Person Name", [45, 80, 125, 95], 19),
        line("person@example.org", [45, 97, 180, 112], 19),
        line("arXiv:2000.00001v1", [8, 160, 27, 420], 195),
        line("Abstract", [270, 200, 325, 214]),
        line("We introduce a model with a testable objective.", [50, 225, 400, 238]),
        line("We report full details in the following sections.", [50, 239, 402, 252]),
        line("1", [50, 269, 57, 281]),
        line("Introduction", [65, 268, 152, 282], 13),
        line("1.1 Methods", [50, 302, 153, 313], 9),
        line("1.1.1 Evaluation", [50, 328, 179, 340], 10),
        line("Figure 1: Results", [50, 409, 345, 430], 20),
        line("1", [295, 750, 305, 763], 18),
    ]
    paragraphs = _ocr_paragraphs(lines, 600, 800)
    headings = [block for block in paragraphs if block["ocr_heading_level"]]
    assert [(block["text"], block["ocr_heading_level"]) for block in headings] == [
        ("Abstract", 1), ("1 Introduction", 1), ("1.1 Methods", 2), ("1.1.1 Evaluation", 3),
    ]
    heading = headings[1]
    assert heading["rect"] == [50, 268, 152, 282]
    assert [item["text"] for item in heading["ocr_lines"]] == ["1", "Introduction"]
    assert heading["ocr_lines"][0]["bbox"] == [0.083333, 0.33625, .095, 0.35125]
    assert {item["text"] for block in paragraphs for item in block["ocr_lines"]} == {item["text"] for item in lines}


def test_scanned_columns_ignore_rotated_and_tabular_text_and_keep_both_section_margins():
    lines = [
        line("2 Approach", [55, 90, 152, 104]),
        line("Long text flows down the left column first.", [55, 130, 268, 143]),
        line("The paragraph continues at this margin.", [55, 146, 268, 160]),
        line("2.1 Architecture", [55, 185, 187, 200]),
        line("2 Maximum", [142, 211, 211, 226], 16),
        line("Embedding", [216, 218, 229, 270], 30),
        line("3 Results", [325, 91, 408, 102]),
        line("Scientific results appear in the right column.", [325, 130, 551, 142]),
        line("More details follow the main results paragraph.", [325, 145, 551, 157]),
    ]
    blocks = _ocr_paragraphs(lines, 600, 800, 2)
    assert [block["text"] for block in blocks if block["ocr_heading_level"]] == [
        "2 Approach", "2.1 Architecture", "3 Results",
    ]


def test_heading_at_top_of_figure_appendix_ends_scanned_bibliography():
    observed = _ocr_paragraphs([
        line("Attention Visualizations", [52, 69, 205, 84], 12),
        line("Weights", [100, 105, 118, 171], 38),
        line("Figure 1: An illustration of the attention weights.", [52, 300, 554, 313], 10),
    ], 600, 800, 5)
    assert [(block["text"], block["ocr_heading_level"]) for block in observed
            if block["ocr_heading_level"]] == [("Attention Visualizations", 1)]
    blocks = [
        {"id": "ref-heading", "type": "heading", "text": "References", "page": 4},
        {"id": "r1", "type": "paragraph", "text": "[1] A genuine reference entry.", "page": 4},
        {"id": "appendix", "type": "heading", "text": "Attention Visualizations", "page": 5,
         "heading_level": 1, "metadata": {"extraction": "rapidocr"}},
        {"id": "caption", "type": "figure_caption", "text": "Figure 1: An illustration of the attention weights.", "page": 5},
    ]
    assert [item["block_id"] for item in _references(blocks)] == ["r1"]
