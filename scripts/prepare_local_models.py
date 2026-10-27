"""Explicit local model setup, never called implicitly by upload or health checks."""
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--semantic", action="store_true")
    args = parser.parse_args()
    if args.ocr or not args.semantic:
        from backend.app.ocr import prepare_ocr
        print(json.dumps({"ocr": prepare_ocr()}, ensure_ascii=False), flush=True)
    if args.semantic or not args.ocr:
        from backend.app.semantic import prepare_model
        print(json.dumps({"semantic": prepare_model()}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
