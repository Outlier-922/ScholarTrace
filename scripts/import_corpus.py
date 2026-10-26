"""Import selected PDFs through the running API and record actual results.

Example:
    python scripts/import_corpus.py --directory <pdf-directory> \
        --names 1706.03762v7.pdf 1706.03762v1.pdf \
        --rename 1706.03762v1.pdf=random-upload.pdf

No source PDF is changed. Use --names to select files, or explicitly opt into
--all. There is no implicit whole-corpus import.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_report(path: Path, run: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise ValueError(f"Existing report is unreadable; refusing to overwrite {path}")
    else:
        report = {"schema_version": 1, "runs": []}
    existing = [item for item in report.get("runs", []) if item.get("id") != run["id"]]
    report["runs"] = existing + [run]
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def import_one(client: httpx.Client, path: Path, source_name: str, upload_name: str,
               wait_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    item: dict[str, Any] = {"source_name": source_name, "upload_name": upload_name,
                            "sha256": sha256(path), "size_bytes": path.stat().st_size,
                            "started_at": timestamp()}
    try:
        with path.open("rb") as stream:
            response = client.post("/api/documents", files={"files": (upload_name, stream, "application/pdf")}, timeout=120)
        response.raise_for_status()
        payload = response.json()
        document = payload["items"][0]
        item.update(document_id=document["id"], job_id=document.get("job_id"),
                    upload_outcome=document.get("upload_outcome"),
                    upload_seconds=round(time.monotonic() - started, 3))
        deadline, previous_progress = time.monotonic() + wait_seconds, None
        while document["status"] in {"queued", "processing"}:
            if time.monotonic() > deadline:
                item["timed_out_waiting"] = True
                break
            time.sleep(.5)
            response = client.get(f"/api/documents/{document['id']}")
            response.raise_for_status()
            document = response.json()
            progress = document.get("progress_pages", 0)
            if progress != previous_progress and (progress % 25 == 0 or document["status"] not in {"queued", "processing"}):
                print(f"  {source_name}: {document['status']} {progress}/{document.get('page_count', '?')}", flush=True)
                previous_progress = progress
        for key in ("title", "status", "page_count", "progress_pages", "blocks_count", "version_label", "arxiv_id",
                    "work_id", "version_id", "parse_run_id", "duplicate_of", "duplicate_kind", "version_recommendation", "warnings", "error"):
            item[key] = document.get(key)
        if document["status"] in {"ready", "partial"}:
            response = client.get(f"/api/documents/{document['id']}/structure", timeout=120)
            response.raise_for_status()
            structure = response.json()
            blocks = structure.get("blocks", [])
            valid = sum(len(block.get("bbox", [])) == 4 and all(0 <= value <= 1 for value in block["bbox"])
                        for block in blocks)
            item["structure"] = {"pages": len(structure.get("pages", [])), "blocks": len(blocks),
                                  "outline_entries": len(structure.get("outline", [])),
                                  "reference_entries": len(structure.get("references", [])),
                                  "normalized_anchor_count": valid,
                                  "page_states": dict(Counter(page["status"] for page in structure.get("pages", [])))}
            page_count = document.get("page_count", 0)
            item["page_samples"] = [{"page": number,
                                      "blocks": sum(block["page"] == number for block in blocks),
                                      "text_chars": sum(len(block["text"]) for block in blocks if block["page"] == number)}
                                     for number in sorted({1, max(1, page_count // 2), page_count})]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        item["status"] = "import_error"
        item["error_type"] = type(exc).__name__
        item["error"] = str(exc)[:500]
    item.update(finished_at=timestamp(), elapsed_seconds=round(time.monotonic() - started, 3))
    print(json.dumps({key: item.get(key) for key in
                      ("source_name", "upload_name", "status", "page_count", "blocks_count", "elapsed_seconds", "duplicate_kind")},
                     ensure_ascii=True), flush=True)
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--names", nargs="+", help="Relative filenames, imported in the specified order")
    selection.add_argument("--all", action="store_true", help="Explicitly import every PDF under the directory")
    parser.add_argument("--rename", action="append", default=[], metavar="SOURCE=UPLOAD_NAME")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--wait-seconds", type=float, default=1800)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "official_import_report.json")
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve(strict=True)
    if not directory.is_dir():
        parser.error("--directory must be a directory")
    names = args.names or [path.relative_to(directory).as_posix() for path in sorted(directory.rglob("*"))
                           if path.is_file() and path.suffix.lower() == ".pdf"]
    renames = {}
    for value in args.rename:
        if "=" not in value:
            parser.error("--rename must use SOURCE=UPLOAD_NAME")
        source, target = value.split("=", 1)
        if not target or Path(target).name != target or "\\" in target:
            parser.error("Upload names must be plain filenames")
        renames[source] = target
    paths = []
    for name in names:
        path = (directory / name).resolve(strict=True)
        if not path.is_relative_to(directory) or path.suffix.lower() != ".pdf":
            parser.error(f"Invalid PDF selection: {name}")
        paths.append((name, path))
    run: dict[str, Any] = {"id": uuid.uuid4().hex, "started_at": timestamp(),
                           "source_directory": str(directory), "api_url": args.api_url,
                           "source_files_modified": False, "items": []}
    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=60, trust_env=False) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        run["backend_version"] = health.json().get("version")
        for name, path in paths:
            run["items"].append(import_one(client, path, name, renames.get(name, path.name), args.wait_seconds))
            save_report(args.output, run)
    run["finished_at"] = timestamp()
    save_report(args.output, run)
    print(f"Report saved to {args.output}", flush=True)
    if any(item["status"] in {"failed", "import_error", "queued", "processing"} for item in run["items"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
