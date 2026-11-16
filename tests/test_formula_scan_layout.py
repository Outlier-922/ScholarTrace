from hashlib import sha256

import pymupdf

from backend.app import formula_native, formula_scan_layout, formulas


def block(identity, text, bbox, kind="paragraph", page=1):
    return {"id": identity, "page": page, "text": text, "bbox": bbox, "type": kind,
            "metadata": {"extraction": "rapidocr", "lines": [{"text": text, "bbox": bbox}]}}


def test_display_fraction_contains_denominator_and_excludes_equation_number():
    rows = [
        block("above", "The attention operation projects the query into a shared space.", [.17,.28,.83,.295]),
        block("base", "Attention(Q,K,V) = softmax(", [.35,.330,.58,.345]),
        block("denom", "√dk", [.58,.339,.619,.360]),
        block("tail", ")V", [.615,.330,.640,.348]),
        block("eq", "(1)", [.78,.330,.81,.347]),
        block("below", "The resulting scores are used to compute contextual representations.", [.17,.375,.83,.391]),
        block("inline", "The temperature is set to T = 4 in the following experiment.", [.17,.391,.83,.407]),
    ]
    [candidate] = formula_scan_layout.locate(rows)
    left, top, right, bottom = candidate["bbox"]
    assert left < .35 and top < .33 and .64 < right < .78 and .36 < bottom < .375
    assert candidate["label"] == "(1)"


def test_two_close_equations_keep_independent_crops():
    rows = [
        block("prose", "In this study we compare the following different periodic functions.", [.17,.31,.82,.326]),
        block("sin", "PE(pos,2i) = sin(pos/1000)", [.37,.36,.63,.38]),
        block("cos", "PE(pos,2i+1) = cos(pos/1000)", [.36,.38,.63,.4]),
        block("next", "The output of each function is used as the positional feature.", [.17,.413,.81,.43]),
    ]
    first, second = formula_scan_layout.locate(rows)
    assert "sin" in first["hint"] and "cos" in second["hint"]
    assert first["bbox"][3] <= second["bbox"][1]


def test_equations_inside_illustrations_and_tables_are_not_suggested():
    rows = [
        block("graphic", "Layer(x) = max(x)", [.38,.14,.62,.16]),
        block("caption", "Figure 1: The individual layers of the model.", [.31,.28,.68,.295], "figure_caption"),
        block("body", "The architecture contains residual blocks and several independent normalization layers.", [.16,.32,.83,.338]),
        block("tabcap", "Table 1: Operations and their associated costs.", [.17,.382,.82,.398], "table_caption"),
        block("cell", "T(n) = n+1", [.38,.455,.49,.478]),
        block("last", "The computational cost depends on the width and the sequence length.", [.17,.51,.82,.528]),
        block("wrapped", "T = 4", [.172,.528,.25,.546]),
    ]
    assert formula_scan_layout.locate(rows) == []


def test_formula_block_response_selects_current_page_and_disables_unready_scans(monkeypatch):
    blocks = [
        block("context", "The lookup table includes the following normalization operation.", [.17,.31,.82,.326]),
        block("equation", "f(x) = x^2", [.41,.36,.56,.38]),
        block("other", "f(x) = x^3", [.41,.36,.56,.38], page=2),
    ]
    pages = [
        {"number": 1, "rotation": 0, "status": "ocr_ready"},
        {"number": 2, "rotation": 0, "status": "ready"},
        {"number": 3, "rotation": 0, "status": "needs_ocr"},
        {"number": 4, "rotation": 90, "status": "ocr_ready"},
    ]
    monkeypatch.setattr(formulas.store, "get_structure", lambda identity, parse_run_id: {
        "blocks": blocks, "pages": pages,
    } if parse_run_id == "run" else None)
    monkeypatch.setattr(formulas.formula_ocr, "runtime_identity", lambda: None)
    result = formulas._page_blocks("doc", 1, "run")
    assert result["scan_ocr_ready"] is False
    assert [item["hint"] for item in result["scan_layout"]["items"]] == ["f(x) = x^2"]
    for page, status in ((2, "native"), (3, "needs_ocr"), (4, "rotated")):
        assert formulas._page_blocks("doc", page, "run")["scan_layout"] == {"status": status, "items": []}


def test_two_columns_keep_separate_formula_labels_and_join_fraction_fragments():
    rows = []
    text = "Nearby prose provides the margins for this article."
    for index in range(5):
        y = .10 + index * .025
        rows.extend([
            block(f"left-{index}", text, [.10, y, .48, y+.014]),
            block(f"right-{index}", text, [.52, y, .90, y+.014]),
        ])
    rows.extend([
        block("fraction", "f(x) =", [.14,.24,.27,.256]),
        block("upper", "i=1", [.28,.226,.325,.239]),
        block("lower", "e−x", [.325,.251,.415,.269]),
        block("left-label", "(1)", [.45,.240,.48,.255]),
        block("right-anchor", "g(x)=x^2 (2)", [.545,.246,.895,.263]),
    ])
    first, second = formula_scan_layout.locate(rows)
    assert first["label"] == "(1)" and .415 < first["bbox"][2] < .45 and first["bbox"][3] >= .269
    assert second["label"] == "(2)" and second["bbox"][0] > .5
    assert second["label_trim"] == "right"


def test_pixel_trim_preserves_equation_and_removes_separated_printed_label(tmp_path):
    path = tmp_path / "equation-with-label.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((335, 310), "g(x) = x", fontsize=13)
        page.insert_text((510, 310), "(2)", fontsize=13)
        doc.save(path)
    digest = sha256(path.read_bytes()).hexdigest()
    crop = {"bbox": [.54,.36,.877,.397], "label": "(2)", "hint": "g(x) = x", "label_trim": "right"}
    [result] = formula_native.trim_equation_labels(path, digest, 1, [crop])
    assert .62 < result["bbox"][2] < .85
    assert "label_trim" not in result and result["label"] == "(2)"
    assert formula_native.render_region(path, digest, 1, result["bbox"]).startswith(b"\x89PNG")


def test_elementwise_product_and_tilde_accent_are_supported_latex():
    latex = r"\tilde{r}_t = W_r \cdot (\mu_r \odot x_t + (1 - \mu_r) \odot x_{t-1})"
    assert formulas.validate_tex(latex) == latex
