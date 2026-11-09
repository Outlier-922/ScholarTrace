"""Compare the ordered text blocks of two stored versions."""

from difflib import SequenceMatcher
from typing import Any

from fastapi import HTTPException

from . import store

PAGE_SIZE = 12
MAX_BLOCKS = 2500
MAX_CHARACTERS = 1_000_000
REVERSED_KIND = {'replace': 'replace', 'insert': 'delete', 'delete': 'insert'}


def compare(document_id: str, other_id: str, offset: int) -> dict[str, Any]:
    if document_id == other_id:
        raise HTTPException(422, '请选择两个不同的文件')

    sources = []
    with store.connection() as conn:
        # Read both document pointers and their blocks from one SQLite snapshot.
        conn.execute('BEGIN')
        docs = {row['id']: dict(row) for row in conn.execute(
            '''SELECT id, work_id, parse_run_id, status, sha256
               FROM documents WHERE deleted=0 AND id IN (?, ?)''',
            (document_id, other_id),
        )}
        if len(docs) != 2:
            raise HTTPException(404, '对照文件不存在或已删除')
        left, right = docs[document_id], docs[other_id]
        if left['work_id'] != right['work_id']:
            raise HTTPException(409, '请选择归入同一作品的文件')
        for doc in (left, right):
            if doc['status'] not in {'ready', 'partial'}:
                raise HTTPException(409, '请等待两个文件完成可读解析')
            rows = conn.execute('''
                SELECT id, page, substr(text, 1, ?) AS text
                FROM blocks WHERE document_id=? AND parse_run_id=?
                    AND type IN ('title', 'heading', 'paragraph') AND text <> ''
                ORDER BY page, order_index, id LIMIT ?
            ''', (MAX_CHARACTERS + 1, doc['id'], doc['parse_run_id'], MAX_BLOCKS + 1))
            blocks = []
            size = 0
            for count, row in enumerate(rows, 1):
                size += len(row['text'])
                if count > MAX_BLOCKS or size > MAX_CHARACTERS:
                    raise HTTPException(422, '文本超过单次版本对照的范围')
                normalized = ' '.join(row['text'].split())
                if normalized:
                    blocks.append((row['id'], row['page'], normalized))
            if not blocks:
                raise HTTPException(409, '至少一侧没有可对照的标题或段落文本')
            sources.append(blocks)

    left_blocks, right_blocks = sources
    # SequenceMatcher breaks ties in input order. Fix that order so swapping
    # baseline and comparison only swaps the sides of each difference.
    reversed_sides = left['sha256'] > right['sha256']
    first, second = (right_blocks, left_blocks) if reversed_sides else (left_blocks, right_blocks)
    matcher = SequenceMatcher(None, [block[2] for block in first],
                              [block[2] for block in second], autojunk=False)
    differences = [item for item in matcher.get_opcodes() if item[0] != 'equal']
    if reversed_sides:
        differences = [(REVERSED_KIND[kind], c, d, a, b) for kind, a, b, c, d in differences]
    return {
        'partial_parse': any(doc['status'] == 'partial' for doc in (left, right)),
        'total': len(differences),
        'previous_offset': max(0, offset - PAGE_SIZE) if offset else None,
        'next_offset': offset + PAGE_SIZE if offset + PAGE_SIZE < len(differences) else None,
        'items': [{
            'kind': kind,
            'left': _excerpt(left, left_blocks[start:end]),
            'right': _excerpt(right, right_blocks[other_start:other_end]),
        } for kind, start, end, other_start, other_end in differences[offset:offset + PAGE_SIZE]],
    }


def _excerpt(doc: dict[str, Any], blocks: list[tuple[str, int, str]]) -> dict[str, Any] | None:
    if not blocks:
        return None
    block_id, page, text = blocks[0]
    return {
        'document_id': doc['id'], 'parse_run_id': doc['parse_run_id'],
        'block_id': block_id, 'page': page, 'last_page': blocks[-1][1],
        'block_count': len(blocks),
        'excerpt': text[:400] + ('…' if len(text) > 400 else ''),
    }
