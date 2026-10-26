"""Generate labelled synthetic PDFs for local checks, never official contest data.

Run from the repository root with:
    python scripts/create_sample_pdfs.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf as fitz


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _line(page: fitz.Page, text: str, y: float, *, size: float = 11) -> float:
    page.insert_text((54, y), text, fontsize=size, fontname="helv")
    return y + size * 1.5


def _paragraph(page: fitz.Page, text: str, y: float) -> float:
    box = fitz.Rect(54, y, 540, y + 110)
    spare = page.insert_textbox(box, text, fontsize=11, fontname="helv", lineheight=1.45)
    if spare < 0:
        raise ValueError("Synthetic paragraph does not fit its layout rectangle")
    return y + 110 - spare + 16


def _write_research_fixture(path: Path, *, revision: int) -> None:
    document = fitz.open()
    document.set_metadata(
        {
            "title": "Synthetic Evidence Study",
            "author": "Fixture Author One; Fixture Author Two",
            "subject": "SYNTHETIC TEST FIXTURE, not a real academic publication",
            "creator": "ScholarTrace fixture generator",
            "creationDate": "D:20260101000000Z",
            "modDate": "D:20260101000000Z",
        }
    )
    page = document.new_page(width=595, height=842)
    y = _line(page, "Synthetic Evidence Study", 60, size=20)
    y = _line(page, "SYNTHETIC TEST FIXTURE - NOT A REAL PAPER", y + 4, size=10)
    y = _line(page, f"Fixture revision {revision}", y, size=10)
    y = _line(page, "Fixture Author One; Fixture Author Two", y + 6)
    y = _line(page, "Synthetic Research Laboratory", y)
    y = _line(page, "1 Introduction", y + 18, size=15)
    y = _paragraph(
        page,
        "This document is a synthetic fixture for testing PDF reading, source anchors, "
        "content deduplication and evidence retrieval. It describes a small imaginary "
        "sequence model. None of the claims should be treated as scientific evidence "
        "outside automated software tests.",
        y + 4,
    )
    y = _line(page, "2 Training Configuration", y + 4, size=15)
    batch_size = 32 if revision == 1 else 64
    y = _paragraph(
        page,
        f"The synthetic training batch size is {batch_size}. The learning rate is 0.0003. "
        "The Adam optimizer settings are beta_1 = 0.9 and beta_2 = 0.98. "
        "The sequence context length is 256 tokens. The training GPU model and GPU "
        "count are deliberately not reported in this fixture.",
        y + 4,
    )
    y = _line(page, "Table 1. Synthetic measurement values", y + 10, size=12)
    for text in (
        "Model             Context tokens        Synthetic score",
        "Fixture-Alpha     256                   72.5",
        "Fixture-Beta      512                   74.0",
    ):
        y = _line(page, text, y, size=10)
    y = _paragraph(
        page,
        "All values above are invented for software validation. They are not benchmark "
        "results, and no experiment was conducted. A table recognition component should "
        "preserve the column meaning rather than infer additional results.",
        y + 10,
    )
    page.insert_text((54, 808), "Synthetic fixture - page 1", fontsize=9)
    page = document.new_page(width=595, height=842)
    y = _line(page, "3 Discussion", 60, size=15)
    y = _paragraph(
        page,
        "Evidence retrieval should identify the exact source passage. A question about "
        "the optimizer should return Training Configuration, while an unsupported "
        "hardware question should remain unanswered. Bibliographic text alone does "
        "not establish the hardware used in this imaginary study.",
        y + 6,
    )
    y = _line(page, "4 Limitations and Future Work", y + 12, size=15)
    y = _paragraph(
        page,
        "This synthetic example does not evaluate long sequence generalization. "
        "A possible future test is to compare context lengths under the same data "
        "and evaluation protocol. This is a fixture direction, not a novelty claim.",
        y + 4,
    )
    y = _line(page, "References", y + 14, size=15)
    _paragraph(
        page,
        "[1] Fixture Writer. Synthetic Background Record. Fictional bibliography entry "
        "used only for testing. Reference-only note: 128 A100 GPUs. This note is not "
        "the hardware configuration of Synthetic Evidence Study.",
        y + 4,
    )
    page.insert_text((54, 808), "Synthetic fixture - page 2", fontsize=9)
    document.save(path, deflate=True, no_new_id=True)
    document.close()


def _write_distinct_fixture(path: Path) -> None:
    document = fitz.open()
    document.set_metadata(
        {
            "title": "Synthetic Retrieval Control Paper",
            "author": "Different Fixture Author",
            "subject": "SYNTHETIC NEGATIVE DEDUPLICATION FIXTURE",
            "creator": "ScholarTrace fixture generator",
        }
    )
    page = document.new_page(width=595, height=842)
    y = _line(page, "Synthetic Retrieval Control Paper", 60, size=19)
    y = _line(page, "SYNTHETIC TEST FIXTURE - NOT A REAL PAPER", y + 10, size=10)
    y = _line(page, "Different Fixture Author", y + 8)
    y = _line(page, "1 Document Retrieval", y + 18, size=15)
    y = _paragraph(
        page,
        "This independent synthetic document concerns document retrieval rather than "
        "sequence model training. The index stores normalized page coordinates and "
        "supports keyword lookup. Shared vocabulary is deliberately present to verify "
        "that two related documents are not automatically merged as the same work.",
        y + 5,
    )
    y = _line(page, "2 Test Conditions", y + 14, size=15)
    _paragraph(
        page,
        "The retrieval index contains seven synthetic records. The query limit is five "
        "results, and the score is a software relevance value rather than an academic "
        "evaluation outcome. There is no GPU training experiment, optimizer comparison "
        "or model accuracy result in this document.",
        y + 5,
    )
    document.save(path, deflate=True, no_new_id=True)
    document.close()


def generate_samples(output_directory: Path) -> dict[str, Path]:
    """Return paths for two revisions, an unrelated paper, a scan, and a corrupt file."""
    output_directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "v1": output_directory / "synthetic_evidence_v1.pdf",
        "v2": output_directory / "synthetic_evidence_v2.pdf",
        "distinct": output_directory / "synthetic_retrieval_control.pdf",
        "scan": output_directory / "synthetic_scan.pdf",
        "corrupt": output_directory / "synthetic_corrupt.pdf",
    }
    for key, revision in (("v1", 1), ("v2", 2)):
        _write_research_fixture(paths[key], revision=revision)
    _write_distinct_fixture(paths["distinct"])

    with fitz.open(paths["v1"]) as source:
        pixels = source[0].get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
        scan = fitz.open()
        page = scan.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=pixels.tobytes("png"))
        scan.set_metadata(
            {
                "title": "Synthetic Image-only Scan",
                "subject": "SYNTHETIC OCR FIXTURE; no native text layer",
            }
        )
        scan.save(paths["scan"], deflate=True, no_new_id=True)
        scan.close()
    paths["corrupt"].write_bytes(b"This is an intentionally corrupt synthetic PDF fixture.\n")

    manifest = {
        "synthetic": True,
        "official_contest_data": False,
        "warning": "All papers, authors, measurements and bibliography entries are fictional test data.",
        "expected_facts": {
            "v1_batch_size": 32,
            "v2_batch_size": 64,
            "learning_rate": 0.0003,
            "beta_1": 0.9,
            "beta_2": 0.98,
            "context_tokens": 256,
            "training_gpu_model": None,
            "training_gpu_count": None,
        },
        "files": [],
    }
    for kind, path in paths.items():
        item = {
            "kind": kind,
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if kind != "corrupt":
            with fitz.open(path) as pdf:
                item["pages"] = len(pdf)
                item["native_text_characters"] = sum(len(page.get_text()) for page in pdf)
        manifest["files"].append(item)
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "data" / "samples")
    arguments = parser.parse_args()
    paths = generate_samples(arguments.output.resolve())
    print("Generated synthetic fixtures (not official contest papers):")
    for kind, path in paths.items():
        print(f"  {kind}: {path}")
    print(f"  manifest: {arguments.output.resolve() / 'manifest.json'}")


if __name__ == "__main__":
    main()
