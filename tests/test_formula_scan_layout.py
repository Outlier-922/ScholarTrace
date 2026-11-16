from backend.app import formula_scan_layout, formulas


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
    result = formulas.blocks("doc", 1, "run")
    assert result["scan_ocr_ready"] is False
    assert [item["hint"] for item in result["scan_layout"]["items"]] == ["f(x) = x^2"]
    for page, status in ((2, "native"), (3, "needs_ocr"), (4, "rotated")):
        assert formulas.blocks("doc", page, "run")["scan_layout"] == {"status": status, "items": []}
