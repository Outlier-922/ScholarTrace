"""Offline cold-index / new-process cache audit of the supplied 541-page book."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def memory_bytes():
    if sys.platform != 'win32':
        import resource
        return {'peak_working_set': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)}
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in
                    ['peak_working_set', 'working_set', 'peak_paged_pool', 'paged_pool', 'peak_nonpaged_pool',
                     'nonpaged_pool', 'pagefile', 'peak_pagefile', 'private_bytes']]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {key: getattr(counters, key) for key in ['peak_working_set', 'working_set', 'private_bytes']}


def source_digest(structure):
    return hashlib.sha256(json.dumps([{k: block[k] for k in ('id', 'text', 'page', 'bbox')} for block in structure['blocks']],
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ['freeze', 'workspace', 'output']:
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--mode', choices=['cold', 'resume', 'warm'], required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve the first run; choose a new output directory.')
    frozen = json.loads(args.freeze.read_text(encoding='utf-8'))
    for name, expected in frozen['files'].items():
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest() != expected:
            raise SystemExit('Frozen file changed: ' + name)
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env', override=False)
    os.environ.update(SCHOLARTRACE_DATA_DIR=str(args.workspace.resolve()), SCHOLARTRACE_ENABLE_DENSE='true',
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', LLM_API_KEY='')
    from backend.app import qa, semantic, store
    source = frozen['source']
    document = store.get_document(source['id'])
    assert document and document['sha256'] == source['sha256']
    structure = store.get_structure(source['id'], parse_run_id=source['parse_run_id'])
    assert source_digest(structure) == source['blocks_sha256']
    assert [page['number'] for page in structure['pages']] == list(range(1, 542))
    assert semantic._cache_model_id() == frozen['embedding_model_id']
    document = {**document, '_retrieval_run': source['parse_run_id']}
    args.output.mkdir(parents=True)
    def save(name, value):
        (args.output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    started = time.monotonic()
    report = {'application_version': frozen['application_version'], 'mode': args.mode, 'pid': os.getpid(),
              'scope': frozen['scope'], 'llm_provider_calls': 0, 'downloads_allowed': False,
              'parsed_pages': len(structure['pages']), 'parsed_blocks': len(structure['blocks']), 'passed': False}
    save('manifest.json', {'freeze': str(args.freeze), 'pid': os.getpid(), 'mode': args.mode,
                            'source': source, 'embedding_model_id': frozen['embedding_model_id']})
    sources = qa._collect_sources([document]); blocks = {b['id']: b for b in structure['blocks']}
    report['eligible_sources'] = len(sources)
    report['eligible_page_count'] = len({s['page'] for s in sources})
    report['full_structure_bytes'] = len(json.dumps(structure, ensure_ascii=False).encode())
    report['paged_loading'] = []
    for number in frozen['pages']:
        begin = time.monotonic()
        page = store.get_structure(source['id'], page=number, parse_run_id=source['parse_run_id'])
        assert page and all(b['page'] == number for b in page['blocks'])
        report['paged_loading'].append({'page': number, 'blocks': len(page['blocks']),
                                       'bytes': len(json.dumps(page, ensure_ascii=False).encode()),
                                       'seconds': round(time.monotonic()-begin, 4)})
    last_log = -1
    def progress(done, total):
        nonlocal last_log
        elapsed = time.monotonic() - started
        memory = memory_bytes()
        save('progress.json', {'done': done, 'total': total, 'elapsed_seconds': round(elapsed, 2), **memory})
        if memory['peak_working_set'] > frozen['limits']['peak_working_set_bytes']:
            raise MemoryError('Predeclared local process memory ceiling exceeded.')
        if elapsed > frozen['limits']['wall_seconds']:
            raise TimeoutError('Predeclared local elapsed-time ceiling exceeded; cached batches retained.')
        percent = int(done * 100 / max(total, 1)) // 10
        if percent != last_log:
            print(json.dumps({'percent_band': percent * 10, 'done': done, 'total': total,
                              'seconds': round(elapsed, 1), 'peak_mib': round(memory['peak_working_set']/2**20, 1)}), flush=True)
            last_log = percent
    try:
        _, before = semantic.search('research evidence', sources)
        report['before'] = before
        if args.mode in {'cold', 'resume'}:
            assert before['missing_chunks'] > 384 and not before['used']
            begin = time.monotonic()
            _, indexed = semantic.search('research evidence', sources, force_index=True, progress=progress)
            report['index_seconds'] = round(time.monotonic() - begin, 3)
        else:
            assert before['used'] and before['missing_chunks'] == 0
            indexed = before
        assert indexed['used'] and indexed['missing_chunks'] == 0
        report['index_report'] = indexed
        report['queries'] = []
        for case in frozen['queries']:
            begin = time.monotonic(); details = {}
            evidence = qa._retrieve(case['question'], [document], 6, details)
            assert details.get('used') and details.get('missing_chunks') == 0
            assert evidence
            for item in evidence:
                block = blocks[item['block_id']]
                assert item['parse_run_id'] == source['parse_run_id']
                assert all(item[k] == block[k] for k in ('text', 'page', 'bbox'))
            report['queries'].append({'id': case['id'], 'question': case['question'],
                                      'seconds': round(time.monotonic()-begin, 3), 'retrieval': details, 'evidence': evidence})
            progress(indexed['indexed_chunks'], indexed['indexed_chunks'])
        report.update(passed=True, elapsed_seconds=round(time.monotonic()-started, 3), memory=memory_bytes())
    except Exception as error:
        report.update(error_type=type(error).__name__, elapsed_seconds=round(time.monotonic()-started, 3), memory=memory_bytes())
        raise
    finally:
        save('results.json', report)
    print(json.dumps({k: report[k] for k in ('mode', 'passed', 'elapsed_seconds', 'memory')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
