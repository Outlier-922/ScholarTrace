"""Opt-in real-provider rehearsal, isolated from the user's library.

Requires --run-real-model; uses the configured provider and may incur charges.
No credentials or provider error bodies are written to the report.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8015)
    parser.add_argument('--run-real-model', action='store_true')
    args = parser.parse_args()
    if not args.run_real_model:
        parser.error('Real model use requires --run-real-model')
    root = Path(__file__).resolve().parents[1]
    work = args.workspace.resolve()
    if work.exists():
        parser.error('Choose a fresh workspace to preserve earlier evidence')
    work.mkdir(parents=True)
    environment = {**os.environ, 'SCHOLARTRACE_DATA_DIR': str(work / 'library'),
                   'SCHOLARTRACE_ENABLE_OCR': 'false', 'SCHOLARTRACE_ENABLE_TABLES': 'true',
                   'SCHOLARTRACE_ENABLE_DENSE': 'false', 'PYTHONUTF8': '1'}
    with (work / 'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app.main:app',
                                    '--host', '127.0.0.1', '--port', str(args.port)],
                                   cwd=root, env=environment, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{args.port}/api', timeout=30, trust_env=False) as client:
                def request(method, path, **kwargs):
                    response = client.request(method, path, **kwargs)
                    if response.is_error: raise RuntimeError(f'HTTP {response.status_code} at {path}')
                    return response.json()

                def wait(read, predicate, seconds):
                    end = time.monotonic() + seconds
                    while time.monotonic() < end:
                        if process.poll() is not None: raise RuntimeError('Rehearsal server stopped')
                        try:
                            value = read()
                            if predicate(value): return value
                        except httpx.ConnectError: pass
                        time.sleep(.3)
                    raise TimeoutError('rehearsal_deadline')

                wait(lambda: request('GET', '/health'), lambda value: True, 30)
                assert request('GET', '/model/status')['ready'], 'Model configuration unavailable'
                assert request('GET', '/documents')['items'] == [], 'Workspace must be empty'
                with args.pdf.open('rb') as stream:
                    queued = request('POST', '/documents', files=[('files', (args.pdf.name, stream, 'application/pdf'))])['items'][0]
                paper = wait(lambda: request('GET', '/documents/' + queued['id']), lambda value: value['status'] in {'ready','partial','failed'}, 90)
                assert paper['status'] in {'ready','partial'}
                task = request('POST', '/research-tasks', json={
                    'goal': '检索这篇 Transformer 论文注意力头数 h 的原文并回答，然后构建引用图谱。完成两项后结束。不要比较，不要综述，不要写作，不要流程图。',
                    'document_ids': [paper['id']], 'planner': 'agent', 'use_model': True,
                    'max_tool_calls': 4, 'max_model_calls': 8, 'max_output_tokens': 14000,
                    'max_active_seconds': 240, 'max_metadata_requests': 0})
                request('POST', '/research-tasks/' + task['id'] + '/control', json={'action': 'start'})
                final = wait(lambda: request('GET', '/research-tasks/' + task['id']),
                             lambda value: value['status'] in {'completed','completed_with_gaps','failed','paused','budget_exhausted','cancelled'}, 270)
                (work / 'task.json').write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
                summary = {'version': request('GET', '/health')['version'], 'task_id': final['id'], 'status': final['status'],
                           'provider': 'real configured provider; identity omitted', 'document_sha256': paper['sha256'],
                           'tools': [step['tool'] for step in final['steps']], 'usage': final['usage'],
                           'decisions': final['agent']['decisions'], 'sources_unchanged': final['sources'] == task['sources'],
                           'gaps': final['gaps']}
                (work / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                if final['status'] not in {'completed','completed_with_gaps'}:
                    raise RuntimeError('Rehearsal did not reach completion; inspect saved results')
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__ == '__main__':
    main()
