import pymupdf

from backend.app import store
from conftest import upload_pdf, wait_for_document


def test_scanned_figure_and_caption_return_separate_page_regions(client, tmp_path):
    source = pymupdf.open()
    page = source.new_page(width=400, height=600)
    page.insert_text((70, 80), "SYNTHETIC section title")
    page.draw_rect((110, 120, 280, 260), color=(0, 0, 0), fill=(.85, .90, 1))
    page.draw_line((125, 230), (270, 145), color=(.2, .3, .8))
    page.insert_text((75, 290), "Figure 1: A synthetic illustration with an")
    page.insert_text((75, 303), "aligned line of source text.")
    page.insert_text((75, 365), "A separate paragraph with enough words to mark the end of this region.")
    page.insert_text((75, 490), "Figure 2: This caption is unpaired.")
    with pymupdf.open() as scanned:
        scanned_page = scanned.new_page(width=400, height=600)
        scanned_page.insert_image(scanned_page.rect, pixmap=page.get_pixmap())
        scan_path = tmp_path / "scan-figures.pdf"
        scanned.save(scan_path)
    source.close()

    doc = wait_for_document(client, upload_pdf(client, scan_path)["id"])
    identity, run = doc["id"], doc["parse_run_id"]
    route = f"/api/documents/{identity}/scan-figures"
    query = {"page": 1, "parse_run_id": run}
    assert client.get(route, params=query).json()["status"] == "needs_ocr"

    # Stand in for OCR line blocks so this runs without prepared model weights.
    def block(kind, text, box, order):
        return {"id": store.new_id("blk"), "type": kind, "text": text, "bbox": box,
                "order": order, "metadata": {"extraction": "rapidocr"}}

    blocks = [
        block("heading", "SYNTHETIC section title", [.175, .115, .48, .138], 0),
        block("figure_caption", "Figure 1: A synthetic illustration with an", [.188, .466, .70, .488], 1),
        block("paragraph", "aligned line of source text.", [.187, .488, .51, .51], 2),
        block("paragraph", "A separate paragraph with enough words to mark the end of this region.",
              [.188, .587, .914, .614], 3),
        block("figure_caption", "Figure 2: This caption is unpaired.", [.188, .80, .583, .821], 4),
        {**block("figure", "", [0, 0, 1, 1], 5), "metadata": {"extraction": "image_region"}},
    ]
    store.save_page(identity, run, 1, 400, 600, 0, [0, 0, 400, 600], "ocr_ready", blocks)
    result = client.get(route, params=query)
    assert result.status_code == 200, result.text
    paired, unpaired = result.json()["items"]
    assert paired["caption_block_ids"] == [b["id"] for b in blocks[1:3]]
    assert paired["caption_bbox"] == [.187, .466, .70, .51]
    x0, y0, x1, y1 = paired["figure_bbox"]
    assert .26 < x0 < .28 and .19 < y0 < .21
    assert .70 < x1 < .72 and .43 < y1 < .45
    assert y1 < paired["caption_bbox"][1]
    assert unpaired["figure_bbox"] is None
    assert client.get(f"{route}/{paired['id']}/image", params=query).content.startswith(b"\x89PNG")
    assert client.get(f"{route}/{unpaired['id']}/image", params=query).status_code == 404
    assert client.get(route, params={**query, "parse_run_id": "unknown"}).status_code == 422
    assert client.delete(f"/api/documents/{identity}").status_code == 200
    assert client.get(f"{route}/{paired['id']}/image", params=query).status_code == 404
