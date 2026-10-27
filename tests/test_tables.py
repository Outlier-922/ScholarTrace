"""Source geometry and numerical integrity checks for table candidates."""

from __future__ import annotations

import pymupdf
import pytest

from backend.app.tables import TableExtractionError, extract_tables, serialize_table


@pytest.fixture
def grid_pdf(tmp_path):
    """A labelled software fixture, never a scientific measurement."""
    path = tmp_path / "synthetic_grid_table.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 40), "SYNTHETIC TABLE FIXTURE - NO REAL EXPERIMENT", fontsize=11)
        x_values, y_values = [60, 240, 360, 500], [100, 135, 170, 205]
        for x in x_values:
            page.draw_line((x, y_values[0]), (x, y_values[-1]), width=0.8)
        for y in y_values:
            page.draw_line((x_values[0], y), (x_values[-1], y), width=0.8)
        rows = [["Model", "Accuracy (%)", "Latency (ms)"], ["Synthetic Alpha", "72.5", "12.0"], ["Synthetic Beta", "74.0", ""]]
        for row_number, row in enumerate(rows):
            for column, text in enumerate(row):
                page.insert_text((x_values[column] + 7, y_values[row_number] + 23), text, fontsize=10)
        document.save(path)
    return path


def test_grid_table_preserves_header_values_empty_cell_and_geometry(grid_pdf):
    with pymupdf.open(grid_pdf) as document:
        tables = extract_tables(document[0])
    assert len(tables) == 1
    block = tables[0]
    metadata = block["metadata"]
    assert metadata["rows"] == [["Model", "Accuracy (%)", "Latency (ms)"], ["Synthetic Alpha", "72.5", "12.0"], ["Synthetic Beta", "74.0", ""]]
    assert metadata["row_count"] == 3
    assert metadata["col_count"] == 3
    assert metadata["header"]["names"] == ["Model", "Accuracy (%)", "Latency (ms)"]
    assert metadata["verification_status"] == "unverified"
    assert metadata["confidence"] is None
    assert len(metadata["cells"]) == 9
    assert block["bbox"] == pytest.approx([0.1, 0.125, 5 / 6, 0.25625], abs=1e-6)
    assert "Accuracy (%) = 72.5" in block["text"]
    assert "Latency (ms) = [empty cell]" in block["text"]
    assert "Latency (ms) = 74.0" not in block["text"]
    for cell in metadata["cells"]:
        assert cell["bbox"] is not None
        assert all(0 <= value <= 1 for value in cell["bbox"])


def test_prose_without_grid_is_not_implicitly_a_table():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((60, 60), "SYNTHETIC PROSE ONLY. This paragraph has numbers 32 and 64.")
        assert extract_tables(page) == []


def test_text_strategy_requires_explicit_region(grid_pdf):
    with pymupdf.open(grid_pdf) as document:
        with pytest.raises(TableExtractionError, match="explicit source region"):
            extract_tables(document[0], strategy="text")


def test_source_coordinates_are_unrotated_even_when_viewer_rotates(grid_pdf):
    with pymupdf.open(grid_pdf) as document:
        original = extract_tables(document[0])[0]
        document[0].set_rotation(90)
        rotated = extract_tables(document[0])[0]
        assert document[0].rotation == 90
    assert rotated["bbox"] == original["bbox"]
    assert rotated["metadata"]["rows"] == original["metadata"]["rows"]


def test_serialization_does_not_fill_null_or_blank_cells():
    result = serialize_table([["A", None, ""], ["B", "4.0", "8.0"]], ["Model", "Metric", "Metric [column 3]"], page_number=2)
    assert "Metric = [merged or missing cell]" in result
    assert "Metric [column 3] = [empty cell]" in result
    assert "Metric = 4.0" in result
    assert "Metric [column 3] = 8.0" in result


def test_invalid_configuration_fails_explicitly(grid_pdf):
    with pymupdf.open(grid_pdf) as document:
        page = document[0]
        with pytest.raises(ValueError):
            extract_tables(page, strategy="magic")
        with pytest.raises(ValueError):
            extract_tables(page, max_tables=0)
        with pytest.raises(TableExtractionError):
            extract_tables(page, clip=(-1, 0, 300, 200))


def test_engine_failure_is_not_reported_as_no_tables(grid_pdf, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic injected table error")

    monkeypatch.setattr(pymupdf.Page, "find_tables", fail)
    with pymupdf.open(grid_pdf) as document:
        with pytest.raises(TableExtractionError) as error:
            extract_tables(document[0])
    assert error.value.code == "engine_error"


def _matrix_document(rows, *, caption="", width=600):
    """A vector grid with explicit fictional contents, kept only in memory."""
    document = pymupdf.open()
    page = document.new_page(width=width, height=800)
    page.insert_text((40, 35), "SYNTHETIC GRID CLASSIFICATION FIXTURE", fontsize=9)
    columns = len(rows[0])
    cell_width = (width - 100) / columns
    for column in range(columns + 1):
        x = 40 + column * cell_width
        page.draw_line((x, 100), (x, 100 + 22 * len(rows)))
    for row in range(len(rows) + 1):
        y = 100 + row * 22
        page.draw_line((40, y), (width - 60, y))
    for row_number, row in enumerate(rows):
        for column, value in enumerate(row):
            if value:
                page.insert_text((43 + column * cell_width, 114 + row_number * 22), value, fontsize=6)
    if caption:
        page.insert_text((40, 125 + len(rows) * 22), caption, fontsize=10)
    return document


def test_sparse_diagram_grid_is_filtered_with_a_reason():
    rows = [[""] * 12 for _ in range(8)]
    for row, column, value in [(1, 1, "The"), (2, 3, "Law"), (3, 5, "will"), (4, 7, "be"), (5, 9, "just")]:
        rows[row][column] = value
    with _matrix_document(rows, caption="Figure 1: Synthetic attention graphic, not a data table.") as document:
        assert len(extract_tables(document[0], filter_suspected_figures=False)) == 1
        rejected = []
        assert extract_tables(document[0], diagnostics=rejected) == []
    assert rejected[0]["reason"] == "sparse_fragmented_graphic"
    assert rejected[0]["structural_signals"]["figure_caption_on_page"] is True


def test_wide_real_grid_is_not_filtered_by_column_count():
    rows = [[f"Metric{column:02d}" for column in range(40)],
            [f"{column}.1" for column in range(40)], [f"{column}.2" for column in range(40)]]
    with _matrix_document(rows, width=1500) as document:
        tables = extract_tables(document[0])
    assert len(tables) == 1
    assert tables[0]["metadata"]["col_count"] == 40
    assert tables[0]["metadata"]["rows"] == rows


def test_sparse_numeric_matrix_survives_weak_headers():
    rows = [[""] * 12 for _ in range(8)]
    for row in range(1, 8):
        rows[row][row] = str(row)
    with _matrix_document(rows) as document:
        tables = extract_tables(document[0])
    assert len(tables) == 1
    assert tables[0]["metadata"]["structural_signals"]["numeric_cell_ratio"] == 1


def test_table_caption_preserves_sparse_categorical_table():
    rows = [[""] * 12 for _ in range(8)]
    for row in range(1, 8):
        rows[row][row] = "Y"
    with _matrix_document(rows, caption="Table 1: Synthetic sparse categorical data.") as document:
        tables = extract_tables(document[0])
    assert len(tables) == 1
    assert tables[0]["metadata"]["structural_signals"]["nearby_table_caption"] is True
