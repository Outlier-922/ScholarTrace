"""Validate a frozen book cache completely before appending it to local data."""
import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_long_book import source_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['freeze', 'source', 'target', 'backup', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.backup.exists() or args.output.exists():
        parser.error('Preserve existing backup and proof; choose fresh paths.')
    source_root, target_root = args.source.resolve(), args.target.resolve()
    if source_root == target_root or not (target_root/'scholartrace.sqlite3').is_file():
        parser.error('A separate existing target workspace is required.')
    frozen = json.loads(args.freeze.read_text(encoding='utf-8'))
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env', override=False)
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(target_root), LLM_API_KEY='', HF_HUB_OFFLINE='1')
    from backend.app import store, qa, semantic
    source = frozen['source']; document = store.get_document(source['id'])
    assert document and document['sha256'] == source['sha256']
    structure = store.get_structure(source['id'], parse_run_id=source['parse_run_id'])
    assert source_digest(structure) == source['blocks_sha256']
    assert semantic._cache_model_id() == frozen['embedding_model_id']
    sources = qa._collect_sources([{**document, '_retrieval_run': source['parse_run_id']}])
    chunks = {c['chunk_id']: c for c in semantic._chunks(sources, semantic._tokenizer())}
    with sqlite3.connect(source_root/'vectors.sqlite3') as connection:
        rows = connection.execute('SELECT chunk_id,model,document_id,parse_run_id,block_id,vector FROM vectors WHERE document_id=? AND model=?',
                                  (source['id'], frozen['embedding_model_id'])).fetchall()
    assert {row[0] for row in rows} == set(chunks) and len(rows) == len(chunks)
    for identity, model, doc, run, block, blob in rows:
        assert model == frozen['embedding_model_id'] and doc == source['id'] and run == source['parse_run_id']
        assert chunks[identity]['block_id'] == block and len(blob) == 384 * 4
        values = array('f'); values.frombytes(blob)
        assert all(math.isfinite(v) for v in values) and abs(math.fsum(v*v for v in values)-1) < .001
    before = store.stats()
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with semantic._connection() as connection:
        with sqlite3.connect(args.backup) as backup:
            connection.backup(backup)
        connection.execute('BEGIN IMMEDIATE')
        existing = connection.execute('SELECT count(*) FROM vectors WHERE document_id=? AND model=?',
                                      (source['id'], frozen['embedding_model_id'])).fetchone()[0]
        # This audited append targets an unindexed book; do not replace a cache
        # that another process may have prepared in the meantime.
        assert existing == 0
        connection.executemany('INSERT INTO vectors VALUES(?,?,?,?,?,?)', rows)
        actual = connection.execute('SELECT count(*) FROM vectors WHERE document_id=? AND model=?',
                                    (source['id'], frozen['embedding_model_id'])).fetchone()[0]
        assert actual == len(chunks)
    assert store.stats() == before
    proof = {'application_version': frozen['application_version'], 'document_id': source['id'],
             'parse_run_id': source['parse_run_id'], 'source_snapshots_match': True,
             'validated_and_inserted_vectors': len(rows), 'dimension': 384, 'all_finite_normalized': True,
             'previous_book_vector_count': existing, 'library_stats_unchanged': before,
             'backup': str(args.backup), 'backup_sha256': hashlib.sha256(args.backup.read_bytes()).hexdigest(),
             'provider_calls': 0, 'runner_lf_sha256': hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps({'validated_vectors': len(rows), 'library_stats': before, 'provider_calls': 0}))


if __name__ == '__main__':
    main()
