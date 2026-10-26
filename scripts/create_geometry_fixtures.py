"""Create labelled one-page rotation/CropBox fixtures for PDF anchor QA.

The red rectangles are baked into each PDF. A correctly transformed frontend
highlight should land on the same text after zooming and rotation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
ANCHORS = (
    ("ANCHOR TOP LEFT", (110, 160)),
    ("ANCHOR CENTER 256", (180, 360)),
    ("ANCHOR LOWER RIGHT", (300, 650)),
)


def generate_geometry_samples(output_directory: Path) -> dict[str, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    configurations = (
        ("rotated_90", 90, None),
        ("cropped", 0, (60, 80, 540, 720)),
        ("rotated_270_cropped", 270, (60, 80, 540, 720)),
    )
    paths: dict[str, Path] = {}
    manifest = {
        "synthetic": True,
        "official_contest_data": False,
        "purpose": "Verify PDF source anchors on rotated and cropped pages; red boxes are source content.",
        "coordinate_contract": "bbox is normalized top-left unrotated CropBox coordinates; apply page rotation in viewer.",
        "files": [],
    }
    for name, rotation, cropbox in configurations:
        path = output_directory / f"synthetic_geometry_{name}.pdf"
        document = pymupdf.open()
        page = document.new_page(width=600, height=800)
        page.insert_text((100, 110), "SYNTHETIC GEOMETRY FIXTURE", fontsize=16)
        for text, point in ANCHORS:
            page.insert_text(point, text, fontsize=12)
            rectangle = page.search_for(text)[0]
            page.draw_rect(rectangle + (-3, -3, 3, 3), color=(0.85, 0.12, 0.12), width=1)
        page.insert_text((110, 690), "Fictional test content. No academic claims.", fontsize=10)
        if cropbox:
            page.set_cropbox(pymupdf.Rect(cropbox))
        page.set_rotation(rotation)
        document.set_metadata({"title": f"Synthetic Geometry Fixture {name}", "subject": "SYNTHETIC PDF COORDINATE TEST"})
        document.save(path, deflate=True, no_new_id=True)
        document.close()
        paths[name] = path

        with pymupdf.open(path) as source:
            page = source[0]
            width, height = page.cropbox.width, page.cropbox.height
            item = {
                "filename": path.name,
                "rotation": page.rotation,
                "cropbox": list(page.cropbox),
                "unrotated_size": [width, height],
                "display_size": [page.rect.width, page.rect.height],
                "anchors": [],
            }
            for text, _ in ANCHORS:
                rectangle = page.search_for(text)[0]
                display = rectangle * page.rotation_matrix
                item["anchors"].append(
                    {
                        "text": text,
                        "normalized_unrotated_bbox": [rectangle.x0 / width, rectangle.y0 / height, rectangle.x1 / width, rectangle.y1 / height],
                        "normalized_display_bbox": [display.x0 / page.rect.width, display.y0 / page.rect.height, display.x1 / page.rect.width, display.y1 / page.rect.height],
                    }
                )
            page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25)).save(path.with_suffix(".png"))
            manifest["files"].append(item)
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "samples" / "geometry")
    arguments = parser.parse_args()
    paths = generate_geometry_samples(arguments.output.resolve())
    for name, path in paths.items():
        print(f"{name}: {path}")
    print(f"Manifest and rendered PNG references: {arguments.output.resolve()}")


if __name__ == "__main__":
    main()
