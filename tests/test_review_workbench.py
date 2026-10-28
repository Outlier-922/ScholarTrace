"""Review page integrity tests use fictional content, never grade real answers."""
import hashlib
import json
import re

import pytest

from scripts.build_review_workbench import collect, render
from scripts.eval_core import digest


def fixture_data():
    source = {'title': 'SYNTHETIC paper', 'work': 'fictional', 'sha256': 'fake',
              'split': 'development', 'version_label': 'v1'}
    case = {'id': 'SYNTHETIC01', 'split': 'development', 'sources': ['sample'],
            'question': 'SYNTHETIC question?', 'expected_facts': ['Fictional count 7.'],
            'gold_groups': [[{'source': 'sample', 'page': 1, 'bbox': [0, 0, 1, 1],
                              'text_sha256': 'fake-text-hash'}]]}
    response = {'mode': 'llm', 'answer': 'Fictional count 7.',
                'claims': [{'text': 'Fictional count 7.', 'evidence_ids': ['ev-synthetic']}],
                'evidence': [{'id': 'ev-synthetic', 'title': 'SYNTHETIC paper', 'page': 1,
                              'text': 'Fictional count 7.', 'bbox': [0, 0, 1, 1]}]}
    row = {'case_id': case['id'], 'case_sha256': digest(case),
           'response_sha256': digest(response), 'response': response, 'live_model_requested': True}
    return {'sources': {'sample': source}, 'cases': [case]}, [row]


def test_page_preserves_original_hashes_and_pending_state():
    pack, rows = fixture_data()
    data = collect(pack, rows, 'synthetic-run')
    assert data['items'][0]['response_sha256'] == rows[0]['response_sha256']
    assert data['items'][0]['expected_facts'] == ['Fictional count 7.']
    html = render(data)
    assert "status:'pending'" in html and "judgment:null" in html
    assert '不会联网、调用模型或自动判正确' in html


@pytest.mark.parametrize('part', ['case', 'response'])
def test_stale_inputs_are_rejected(part):
    pack, rows = fixture_data()
    if part == 'case':
        pack['cases'][0]['question'] += ' changed'
    else:
        rows[0]['response']['answer'] += ' changed'
    with pytest.raises(ValueError, match='changed_since_run'):
        collect(pack, rows, 'synthetic-run')


def test_duplicate_or_empty_run_rejected():
    pack, rows = fixture_data()
    for values in ([], rows * 2):
        with pytest.raises(ValueError, match='empty_or_duplicate'):
            collect(pack, values, 'synthetic-run')


def test_untrusted_text_cannot_break_out_of_json_script():
    pack, rows = fixture_data()
    attack = '</script><img src="https://example.invalid" onerror="alert(1)">&\u2028'
    rows[0]['response']['answer'] = attack
    rows[0]['response_sha256'] = digest(rows[0]['response'])
    html = render(collect(pack, rows, 'synthetic-run'))
    embedded = re.search(r'type="application/json">(.*?)</script>', html, re.S).group(1)
    assert '<' not in embedded and '&' not in embedded and attack not in html
    assert json.loads(embedded)['items'][0]['answer'] == attack
    from html import unescape
    assert 'innerHTML' not in html and "connect-src 'none'" in unescape(html)


def test_csp_hashes_match_only_executable_script_and_style():
    import base64
    pack, rows = fixture_data()
    html = render(collect(pack, rows, 'synthetic-run'))
    for tag in ('script', 'style'):
        code = re.search(fr'<{tag}>(.*?)</{tag}>', html, re.S).group(1)
        encoded = base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
        assert f'sha256-{encoded}' in html
    assert 'fetch(' not in html and 'XMLHttpRequest' not in html


def test_failed_request_remains_reviewable_without_claims():
    pack, rows = fixture_data()
    rows[0].pop('response'); rows[0].pop('response_sha256'); rows[0]['error_type'] = 'SyntheticError'
    item = collect(pack, rows, 'synthetic-run')['items'][0]
    assert item['mode'] == 'SyntheticError' and item['claims'] == []
    assert item['response_sha256'] is None
