"""Check the parser's source-coordinate contract for rotation and CropBox."""

import json

import pytest

from conftest import upload_pdf, wait_for_document
from scripts.create_geometry_fixtures import generate_geometry_samples


@pytest.fixture(scope="module")
def geometry_samples(tmp_path_factory):
    output = tmp_path_factory.mktemp("synthetic-geometry")
    paths = generate_geometry_samples(output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    return paths, {item["filename"]: item for item in manifest["files"]}


@pytest.mark.parametrize("kind", ["rotated_90", "cropped", "rotated_270_cropped"])
def test_rotated_and_cropped_source_boxes_match_unrotated_crop_coordinates(client, geometry_samples, kind):
    paths, expected = geometry_samples
    path = paths[kind]
    reference = expected[path.name]
    document = wait_for_document(client, upload_pdf(client, path)["id"])
    assert document["status"] == "ready", document
    response = client.get(f"/api/documents/{document['id']}/structure")
    assert response.status_code == 200, response.text
    structure = response.json()
    page = structure["pages"][0]
    assert page["rotation"] == reference["rotation"]
    assert [page["width"], page["height"]] == reference["unrotated_size"]
    for anchor in reference["anchors"]:
        block = next(block for block in structure["blocks"] if anchor["text"] in block["text"])
        assert block["bbox"] == pytest.approx(anchor["normalized_unrotated_bbox"], abs=0.000002)
