import hashlib

import pymupdf

from backend.app import store
from backend.app.scan_figures import analyze
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


def test_caption_can_precede_nearby_raster_figure(tmp_path):
    path = tmp_path / "above-caption.pdf"
    with pymupdf.open() as drawing, pymupdf.open() as scanned:
        page = drawing.new_page(width=400, height=600)
        page.insert_text((60, 99), "Figure 1: A synthetic plot.")
        page.draw_rect((90, 121, 250, 254), color=(0, 0, 0), fill=(.86, .93, 1))
        page.draw_line((105, 230), (232, 138), color=(.2, .3, .8))
        page.insert_text((60, 310), "A separate text paragraph divides this image from the next caption.")
        page.insert_text((60, 495), "Figure 2: This caption is unpaired.")
        raster = scanned.new_page(width=400, height=600)
        raster.insert_image(raster.rect, pixmap=page.get_pixmap())
        scanned.save(path)
    blocks = [
        {"id":"above","type":"figure_caption","text":"Figure 1: A synthetic plot.",
         "bbox":[.15,.145,.50,.172],"metadata":{"extraction":"rapidocr"}},
        {"id":"paragraph","type":"paragraph","text":"A separate text paragraph divides this image from the next caption.",
         "bbox":[.15,.49,.88,.526],"metadata":{"extraction":"rapidocr"}},
        {"id":"unpaired","type":"figure_caption","text":"Figure 2: This caption is unpaired.",
         "bbox":[.15,.80,.57,.835],"metadata":{"extraction":"rapidocr"}},
    ]
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    paired, unpaired = analyze(path, sha, 1, blocks)
    assert paired["figure_relation"] == "below_caption"
    assert .19 < paired["figure_bbox"][1] < .21
    assert .42 < paired["figure_bbox"][3] < .44
    assert analyze(path, sha, 1, blocks, preview="above").startswith(b"\x89PNG")
    assert unpaired["figure_bbox"] is None


def test_caption_on_following_page_keeps_previous_page_crop(tmp_path):
    path = tmp_path / "split-figure.pdf"
    with pymupdf.open() as pdf:
        first = pdf.new_page(width=400,height=600)
        first.draw_rect((80,385,320,540), color=(0,0,0), fill=(.85,.90,1))
        second = pdf.new_page(width=400,height=600)
        second.insert_text((67,92),"Figure 1: Source on the previous page.")
        pdf.save(path)
    caption = {"id":"caption","type":"figure_caption","text":"Figure 1: Source on the previous page.",
               "bbox":[.16,.13,.63,.17],"metadata":{"extraction":"rapidocr"}}
    scan = {"id":"page-image","type":"figure","text":"","bbox":[0,0,1,1],"metadata":{"extraction":"image_region"}}
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    item, = analyze(path,sha,2,[caption],previous_blocks=[scan])
    assert item['figure_page'] == 1 and item['figure_relation'] == 'previous_page'
    assert analyze(path,sha,2,[caption],preview='caption',previous_blocks=[scan]).startswith(b'\x89PNG')
    assert not analyze(path,sha,2,[{**caption,'bbox':[.16,.27,.63,.31]}],previous_blocks=[scan])[0]['figure_bbox']
