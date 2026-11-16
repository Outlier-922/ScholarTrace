"""Numeric in-text citations linked to immutable bibliography source positions."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query

from . import ocr, qa, source_scope, store

router = APIRouter(prefix="/api", tags=["reading"])
MARKER = re.compile(r"\[(\d{1,4}(?:\s*[,;–—-]\s*\d{1,4})*)\]")


def expand_marker(value: str) -> list[str]:
    result = []
    for part in re.split(r"[,;]", value):
        interval = re.fullmatch(r"\s*(\d+)\s*[-–—]\s*(\d+)\s*", part)
        if interval:
            start, end = map(int, interval.groups())
            if end < start or end - start > 30:
                continue
            result.extend(str(number) for number in range(start, end + 1))
        elif part.strip().isdigit():
            result.append(str(int(part)))
    return list(dict.fromkeys(result))[:40]


def citation_anchor(block, match, parse_run_id, reference_ids, line_spans=()):
    anchor = {
        "id": f"cite_{block['id']}_{match.start()}",
        "marker_text": match.group(),
        "page": block["page"],
        "block_id": block["id"],
        "bbox": block["bbox"],
        "offset": match.start(),
        "context": block["text"][max(0, match.start() - 100):match.end() + 100],
        "parse_run_id": parse_run_id,
        "reference_ids": reference_ids,
    }
    line = next((item for item in line_spans if item["start"] <= match.start() and match.end() <= item["end"]), None)
    if line:
        anchor["line_bbox"] = line["bbox"]
    return anchor


def citation_map(document_id: str, parse_run_id: str | None = None):
    structure = store.get_structure(document_id, parse_run_id=parse_run_id)
    if structure is None:
        return None
    blocks = {block["id"]: block for block in structure["blocks"]}
    references = []
    by_marker = {}
    for reference in structure["references"]:
        block = blocks.get(reference["block_id"])
        if not block:
            continue
        item = {**reference, "bbox": block["bbox"], "parse_run_id": structure["parse_run_id"], "backlinks": [], "status": "unverified_reference"}
        if reference.get("source_spans"):
            item["line_bbox"] = reference["source_spans"][0]["bbox"]
        references.append(item)
        if reference.get("marker"):
            by_marker.setdefault(str(int(reference["marker"])), []).append(item)
    links = []
    bibliography = source_scope.reference_block_ids(structure)
    for block in structure["blocks"]:
        if block["id"] in bibliography or (qa._is_reference(block) and block.get("metadata", {}).get("extraction") != "rapidocr"):
            continue
        line_spans = ocr.source_line_spans(block)
        for match in MARKER.finditer(block["text"]):
            numbers = expand_marker(match.group(1))
            targets = [by_marker[number][0] for number in numbers if len(by_marker.get(number, [])) == 1]
            if not targets:
                continue
            source = citation_anchor(block, match, structure["parse_run_id"], [item["id"] for item in targets], line_spans)
            links.append(source)
            for target in targets:
                target["backlinks"].append(source)
    return {"document_id": document_id, "parse_run_id": structure["parse_run_id"], "links": links,
            "references": references, "warnings": ["引用关系由正文数字标记与参考文献条目匹配；条目内容仍需核验。作者年份引用暂保留列表，不强行匹配。"]}


@router.get("/documents/{document_id}/citations")
def get_citations(document_id: str, parse_run_id: str | None = Query(default=None, max_length=100)):
    result = citation_map(document_id, parse_run_id)
    if result is None:
        raise HTTPException(404, "文献或解析记录不存在")
    return result
