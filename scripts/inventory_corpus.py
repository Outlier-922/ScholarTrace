"""Read-only inventory of an explicitly selected PDF corpus.

Only short bibliographic metadata and counts are written; source PDFs are never
modified or copied. Paths are supplied by the caller, never embedded here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf as fitz


ROOT = Path(__file__).resolve().parents[1]
ARXIV = re.compile(r"\barxiv\s*:\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: Path, base: Path) -> dict[str, Any]:
    record: dict[str, Any] = {"name": path.relative_to(base).as_posix(),
                              "size_bytes": path.stat().st_size, "sha256": file_hash(path)}
    try:
        with fitz.open(path) as pdf:
            if pdf.needs_pass:
                return {**record, "status": "encrypted", "page_count": len(pdf)}
            if not len(pdf):
                return {**record, "status": "empty", "page_count": 0}
            page = pdf[0]
            text = page.get_text("text")
            # Never infer paper identity from a filename or an arbitrary
            # reference entry. Only an explicit first-page arXiv label is used.
            first_body = re.split(r"(?im)^\s*(?:references|bibliography|参考文献)\s*$", text, maxsplit=1)[0]
            identity = ARXIV.search(first_body[:5000])
            metadata_title = re.sub(r"\s+", " ", (pdf.metadata or {}).get("title", "")).strip()
            title_source = "pdf_metadata" if metadata_title else None
            if not metadata_title or re.match(r"^(untitled|microsoft|latex|document|word|powerpoint)", metadata_title, re.I):
                candidates = []
                for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES).get("blocks", []):
                    if block.get("type") != 0 or block["bbox"][1] > page.rect.height * .5:
                        continue
                    spans = [span for line in block.get("lines", []) for span in line.get("spans", [])]
                    value = " ".join(span.get("text", "").strip() for span in spans).strip()
                    if 8 <= len(value) <= 500 and not ARXIV.search(value):
                        candidates.append((max((span.get("size", 0) for span in spans), default=0), value))
                if candidates:
                    metadata_title = max(candidates, key=lambda value: value[0])[1]
                    title_source = "first_page_layout_candidate"
            image_count = len(page.get_image_info())
            record.update(status="readable", page_count=len(pdf), title=metadata_title[:500] or None,
                          title_source=title_source, first_page_text_chars=len(text.strip()),
                          first_page_image_count=image_count,
                          likely_scanned_first_page=len(text.strip()) < 20 and image_count > 0,
                          arxiv_id=identity.group(1) if identity else None,
                          version_label=identity.group(2).lower() if identity and identity.group(2) else None,
                          identity_source="explicit_first_page_arxiv_label" if identity else None)
    except Exception as exc:
        record.update(status="unreadable", error_type=type(exc).__name__)
    return record


def inventory(directory: Path) -> dict[str, Any]:
    directory = directory.expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("--directory must name a directory")
    files = sorted((path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
                   key=lambda path: path.relative_to(directory).as_posix().casefold())
    items = [inspect_pdf(path, directory) for path in files]
    return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "source_directory": str(directory), "source_files_modified": False,
            "file_count": len(items), "total_pages": sum(item.get("page_count", 0) for item in items),
            "total_bytes": sum(item["size_bytes"] for item in items),
            "note": "First-page scan/identity signals are inventory hints, not full OCR or verified publication metadata.",
            "items": items}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "official_inventory.json")
    args = parser.parse_args()
    report = inventory(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PDFs: {report['file_count']}; pages: {report['total_pages']}; bytes: {report['total_bytes']}")
    for item in report["items"]:
        if item.get("likely_scanned_first_page") or item.get("page_count", 0) >= 500 or item.get("arxiv_id") == "1706.03762":
            print(json.dumps({key: item.get(key) for key in
                              ("name", "page_count", "first_page_text_chars", "likely_scanned_first_page", "arxiv_id", "version_label")},
                             ensure_ascii=True))
    print(f"Inventory saved to {args.output}")


if __name__ == "__main__":
    main()
