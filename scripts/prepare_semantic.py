"""Explicitly download/verify the local semantic model; optionally pre-index papers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from backend.app import semantic, store
    from backend.app.qa import _collect_sources
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", action="append", default=[])
    arguments = parser.parse_args()
    print(json.dumps(semantic.prepare_model(), ensure_ascii=False), flush=True)
    if arguments.document_id:
        store.init_db()
        for identity in arguments.document_id:
            document = store.get_document(identity)
            if not document:
                raise SystemExit(f"Unknown document: {identity}")
            sources = _collect_sources([document])
            _, report = semantic.search("research evidence", sources, force_index=True)
            print(json.dumps({"document_id": identity, **report}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
