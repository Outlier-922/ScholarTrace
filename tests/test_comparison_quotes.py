"""Quote-bound comparison regressions; all model outputs here are synthetic."""
import copy
import json
import pytest
from backend.app import comparison, comparison_quotes as quotes, qa, model_client, store
from test_reading_research import paper, configure
from conftest import upload_pdf, wait_for_document


SOURCE = {'id': 'ev_a', 'text': 'We used 8 A100 GPUs for pretraining. Evaluation used 4 V100 GPUs.'}
PROPOSAL = {'dimension': 'hardware', 'value': '8 A100 GPUs', 'conditions': 'pretraining',
            'support_quote': 'We used 8 A100 GPUs for pretraining.', 'evidence_ids': ['ev_a']}


def apply(proposal):
    rows = [{'key': 'hardware', 'cells': [{'document_id': 'a', 'status': 'evidence_only', 'value': None, 'evidence': [SOURCE]}]}]
    comparison._apply_model_cells(rows, 'a', {'cells': [proposal]})
    return rows[0]['cells'][0]


@pytest.mark.parametrize('change,reason', [
    ({'value': '4 V100 GPUs'}, 'value_phrase_not_in_quote'),
    ({'value': '8 H100 GPUs'}, 'value_phrase_not_in_quote'),
    ({'conditions': 'fine-tuning'}, 'conditions_phrase_not_in_quote'),
    ({'support_quote': 'We trained on eight GPUs.'}, 'quote_phrase_not_in_quote'),
    ({'evidence_ids': ['another_paper']}, 'source_not_in_cell'),
    ({'evidence_ids': ['ev_a', 'ev_a']}, 'one_quote_source_required'),
    ({'evidence_ids': []}, 'one_quote_source_required'),
    ({'evidence_ids': [True]}, 'one_quote_source_required'),
    ({'value': {'text': '8 A100 GPUs'}}, 'invalid_fields'),
    ({'conditions': None}, 'invalid_fields'),
    ({'support_quote': 'x' * 1201}, 'invalid_fields'),
])
def test_old_whole_block_checks_do_not_accept_unbound_values(change, reason):
    result = apply({**PROPOSAL, **change})
    assert result['status'] == 'evidence_only' and result['value'] is None
    assert result['model_attempts'][0]['reason'] == reason
    assert result['model_attempts'][0]['proposal'] == {**PROPOSAL, **change}


def test_success_retains_exact_raw_spans_and_no_semantic_pass():
    result = apply(PROPOSAL)
    assert result['status'] == 'quote_bound_candidate' and result['semantic_verified'] is False
    binding = result['quote_binding']
    for key, field in [('quote_range', 'support_quote'), ('value_range', 'value'), ('conditions_range', 'conditions')]:
        assert SOURCE['text'][slice(*binding[key])] == result[field]


def test_whitespace_offsets_are_reversible_without_rewriting_the_source():
    source = {**SOURCE, 'text': '  We used 8\n A100   GPUs for pretraining.  '}
    checked, reason = quotes.check(PROPOSAL, [source], qa._validate_claims)
    assert reason is None and checked['value'] == '8\n A100   GPUs'
    assert source['text'][slice(*checked['quote_binding']['value_range'])] == checked['value']


def test_ambiguous_quote_is_rejected_instead_of_choosing_first_location():
    source = {**SOURCE, 'text': PROPOSAL['support_quote'] * 2}
    assert quotes.check(PROPOSAL, [source], qa._validate_claims)[1] == 'quote_phrase_not_unique'


def test_ambiguous_value_is_rejected_instead_of_picking_a_condition():
    proposal = {**PROPOSAL, 'support_quote': 'We used 8 A100 GPUs and 8 A100 GPUs.'}
    source = {**SOURCE, 'text': proposal['support_quote']}
    assert quotes.check(proposal, [source], qa._validate_claims)[1] == 'value_phrase_not_unique'


def test_numeric_substring_cannot_convert_128_to_8():
    proposal = {**PROPOSAL, 'value': '8', 'conditions': '', 'support_quote': 'We used 128 GPUs.'}
    source = {**SOURCE, 'text': proposal['support_quote']}
    assert quotes.check(proposal, [source], qa._validate_claims)[1] == 'quote_numeric_or_formula_check_rejected'


def test_empty_conditions_do_not_invent_a_stage():
    result = apply({**PROPOSAL, 'conditions': ''})
    assert result['conditions'] == '' and result['quote_binding']['conditions_range'] is None


def test_duplicate_dimension_preserves_attempts_and_accepts_none():
    rows = [{'key': 'hardware', 'cells': [{'document_id': 'a', 'status': 'evidence_only', 'evidence': [SOURCE]}]}]
    comparison._apply_model_cells(rows, 'a', {'cells': [PROPOSAL, {**PROPOSAL, 'value': '4 V100 GPUs'}]})
    cell = rows[0]['cells'][0]
    assert cell['status'] == 'evidence_only' and len(cell['model_attempts']) == 2
    assert all(a['reason'] == 'duplicate_dimension' for a in cell['model_attempts'])


def test_large_rejected_output_has_explicit_bounded_retention():
    cell = apply({**PROPOSAL, 'value': 'x' * 20000})
    assert cell['model_attempts'][0]['proposal_truncated'] is True
    assert len(cell['model_attempts'][0]['proposal']['value']) == 1200


def test_non_object_model_output_retains_a_clear_missing_state():
    rows = [{'key': 'hardware', 'cells': [{'document_id': 'a', 'status': 'evidence_only', 'evidence': [SOURCE]}]}]
    comparison._apply_model_cells(rows, 'a', [])
    assert rows[0]['cells'][0]['model_review'] == 'invalid_cells_format'


@pytest.mark.parametrize('text,expected', [
    ('required 19 GPU years of compute', None),
    ('within 1 V100 GPU day', None),
    ('used 1.5 GPUs', None),
    ('trained on 592 V100 GPUs', '592 V100 GPUs'),
    ('trained on 8 16GB V100 GPUs', '8 16GB V100 GPUs'),
    ('trained on 16 TPU chips', '16 TPU chips'),
    ('trained on 1,024 32GB V100 GPUs', '1,024 32GB V100 GPUs'),
    ('took 33 TPU core-years', None),
])
def test_hardware_excerpt_does_not_treat_compute_time_as_device_count(text, expected):
    matched = comparison.MATCHERS['hardware'].search(text)
    assert (matched.group() if matched else None) == expected


@pytest.fixture
def comparison_papers(client, paper, sample_pdfs):
    second = wait_for_document(client, upload_pdf(client, sample_pdfs['distinct'])['id'])
    return [paper, second]


def test_api_preserves_rejected_response_history_and_export(client, comparison_papers, monkeypatch):
    configure(monkeypatch)
    responses = []
    async def injected(system, payload, **kwargs):
        assert '不翻译或改写' in system
        source = payload['dimensions'][0]['evidence'][0]
        result = {'cells': [{'dimension': 'hardware', 'value': 'IMAGINARY HARDWARE', 'conditions': '',
                            'support_quote': source['raw_text'][:100], 'evidence_ids': [source['id']]}]}
        responses.append(copy.deepcopy(result));return result
    monkeypatch.setattr(model_client, 'chat_json', injected)
    result = client.post('/api/comparisons', json={'document_ids': [p['id'] for p in comparison_papers], 'dimensions': ['hardware']}).json()
    assert len(responses) == 2 and [item['response'] for item in result['model_outputs']] == responses
    assert not result['semantic_verified']
    assert all(not c['model_attempts'][0]['accepted'] for c in result['rows'][0]['cells'])
    assert result['id'] in [v['id'] for v in client.get('/api/comparisons').json()['items']]
    assert client.get('/api/comparisons/' + result['id']).json() == result
    exported = client.get('/api/comparisons/' + result['id'] + '/json')
    assert exported.json() == result and 'attachment' in exported.headers['content-disposition']
    assert '未采用的模型候选' in client.get('/api/comparisons/' + result['id'] + '/markdown').text


def test_api_history_refuses_purged_parse_run(client, comparison_papers):
    result = client.post('/api/comparisons', json={'document_ids': [p['id'] for p in comparison_papers], 'dimensions': ['hardware'], 'use_model': False}).json()
    first = comparison_papers[0]
    # Reprocess first so only an explicitly historical snapshot is purged.
    new = client.post(f"/api/documents/{first['id']}/reprocess", json={'ocr': False, 'tables': False})
    assert new.status_code == 200
    wait_for_document(client, first['id'])
    with store.connection() as connection:
        connection.execute('DELETE FROM blocks WHERE parse_run_id=?', (first['parse_run_id'],))
        connection.execute('DELETE FROM parse_runs WHERE id=?', (first['parse_run_id'],))
    assert client.get('/api/comparisons/' + result['id']).status_code == 404
    assert result['id'] not in [v['id'] for v in client.get('/api/comparisons').json()['items']]


def test_missing_run_during_model_await_is_not_persisted(client, comparison_papers, monkeypatch):
    configure(monkeypatch)
    first = comparison_papers[0]
    async def remove(*args, **kwargs):
        with store.connection() as connection:
            connection.execute('DELETE FROM parse_runs WHERE id=?', (first['parse_run_id'],))
        return {'cells': []}
    monkeypatch.setattr(model_client, 'chat_json', remove)
    response = client.post('/api/comparisons', json={'document_ids': [p['id'] for p in comparison_papers], 'dimensions': ['hardware']})
    assert response.status_code == 409


def test_successful_quote_history_revalidates_offsets(client, comparison_papers, monkeypatch):
    configure(monkeypatch)
    async def injected(system, payload, **kwargs):
        source = payload['dimensions'][0]['evidence'][0]
        quote = source['raw_text'][:180]
        return {'cells': [{'dimension': 'hardware', 'value': ' '.join(quote.split()[:3]),
                           'conditions': '', 'support_quote': quote, 'evidence_ids': [source['id']]}]}
    monkeypatch.setattr(model_client, 'chat_json', injected)
    response = client.post('/api/comparisons', json={'document_ids': [p['id'] for p in comparison_papers], 'dimensions': ['hardware']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(c['status'] == 'quote_bound_candidate' for c in result['rows'][0]['cells'])
    assert client.get('/api/comparisons/' + result['id']).json() == result
    result['rows'][0]['cells'][0]['quote_binding']['quote_range'][0] += 1
    with store.connection() as connection:
        connection.execute('UPDATE comparisons SET payload=? WHERE id=?', (json.dumps(result), result['id']))
    assert client.get('/api/comparisons/' + result['id']).status_code == 404
