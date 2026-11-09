import hashlib

import pymupdf

from backend.app import store
from conftest import upload_pdf, wait_for_document


def test_library_version_suggestion_uses_readable_peers_instead_of_first_upload(client, tmp_path):
    documents = []
    for version in (1, 10, 7, 10):
        path = tmp_path / f'opaque-{len(documents)}.pdf'
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), 'Synthetic version navigation fixture', fontsize=16)
            page.insert_text((72, 100), f'arXiv: 2611.78910v{version}')
            page.insert_text((72, 125), f'This is fixture copy {len(documents)} for the version panel.')
            pdf.save(path)
        doc = wait_for_document(client, upload_pdf(client, path)['id'])
        assert doc['status'] == 'ready'
        documents.append(doc)

    first, newest, older, alternate = documents
    route = f"/api/documents/{older['id']}/work-versions"
    assert older['duplicate_of'] == first['id']
    assert len({d['work_id'] for d in documents}) == 1
    view = client.get(route).json()
    assert view['latest_readable_version'] == 'v10'
    assert set(view['latest_readable_ids']) == {newest['id'], alternate['id']}
    assert {item['id']: item['parse_run_id'] for item in view['items']} == {
        d['id']: d['parse_run_id'] for d in documents
    }

    for doc in (newest, older):
        pdf = client.get(f"/api/documents/{doc['id']}/pdf")
        assert hashlib.sha256(pdf.content).hexdigest() == doc['sha256']

    store.update_document(alternate['id'], status='failed')
    assert client.get(route).json()['latest_readable_ids'] == [newest['id']]
    assert client.delete(f"/api/documents/{newest['id']}").status_code == 200
    view = client.get(route).json()
    assert view['latest_readable_version'] == 'v7'
    assert view['latest_readable_ids'] == [older['id']]
    assert client.get(f"/api/documents/{newest['id']}/work-versions").status_code == 404


def test_version_diff_preserves_exact_source_locations(client, tmp_path):
    docs = []
    for version, lines in (
        (1, ('Stable  boundary between changes.', 'The measured value is 18.')),
        (2, ('Stable boundary between changes.', 'The measured value is 19.', 'New appendix text.')),
    ):
        path = tmp_path / f'comparison-{version}.pdf'
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), 'Synthetic text comparison fixture', fontsize=16)
            page.insert_text((72, 105), f'arXiv: 2611.78911v{version}')
            for index, line in enumerate(lines):
                target = pdf.new_page() if index else page
                target.insert_text((72, 200), line)
            pdf.save(path)
        docs.append(wait_for_document(client, upload_pdf(client, path)['id']))

    left, right = docs
    path = f"/api/documents/{left['id']}/version-diff"
    params = {'other_id': right['id']}
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert not result['partial_parse']
    assert result['total'] == 2
    assert all(change['kind'] == 'replace' for change in result['items'])
    change = result['items'][1]
    assert [change[side]['page'] for side in ('left', 'right')] == [2, 2]
    assert change['right']['last_page'] == 3
    for side, doc, value in (('left', left, 18), ('right', right, 19)):
        source = change[side]
        assert source['document_id'] == doc['id']
        assert source['parse_run_id'] == doc['parse_run_id']
        structure = client.get(f"/api/documents/{doc['id']}/structure", params={
            'page': source['page'], 'parse_run_id': source['parse_run_id'],
        }).json()
        block = next(item for item in structure['blocks'] if item['id'] == source['block_id'])
        assert source['excerpt'] == block['text'] == f'The measured value is {value}.'

    reverse = client.get(f"/api/documents/{right['id']}/version-diff", params={'other_id': left['id']}).json()
    assert reverse['items'] == [
        {'kind': item['kind'], 'left': item['right'], 'right': item['left']} for item in result['items']
    ]

    assert client.get(path, params={'other_id': left['id']}).status_code == 422
    unrelated = store.create_document('unrelated.pdf', '', 'unrelated-version-diff-hash', 1)
    store.update_document(unrelated['id'], status='ready')
    assert client.get(path, params={'other_id': unrelated['id']}).status_code == 409
    assert client.get(path, params={'other_id': 'doc_missing'}).status_code == 404
    store.update_document(right['id'], status='partial')
    assert client.get(path, params=params).json()['partial_parse']
    store.update_document(right['id'], status='failed')
    assert client.get(path, params=params).status_code == 409
    assert client.delete(f"/api/documents/{right['id']}").status_code == 200
    assert client.get(path, params=params).status_code == 404


def test_content_grouping_rejects_conflicting_identity_and_ambiguous_uploads(client, tmp_path):
    common = (
        'The simulated sensor measures drift through a calibrated oscillator.',
        'Every trial uses the same chamber settings and sampling frequency.',
        'Results report the median deviation under a fixed temperature profile.',
        'A second sweep validates the repeatability of the synthetic signal.',
        'The synthetic calibration procedure repeats under identical lab conditions.',
        'A separate log records the threshold and measured instrument variance.',
    )
    title = 'Synthetic Calibration of a Sensor'

    def upload(alias, *, heading=title, author='', marker='', lines=common):
        path = tmp_path / f'{alias}.pdf'
        with pymupdf.open() as pdf:
            pdf.set_metadata({'title': heading, 'author': author, 'subject': alias})
            page = pdf.new_page()
            page.insert_text((72, 72), heading, fontsize=16)
            if marker:
                page.insert_text((72, 105), marker)
            for index, line in enumerate(lines):
                page.insert_text((72, 140 + 27 * index), line)
            pdf.save(path)
        doc = wait_for_document(client, upload_pdf(client, path)['id'])
        assert doc['status'] == 'ready'
        return doc

    root = upload('anonymous-draft', author='Mira Sample')
    revised = upload('opaque-revision', author='M. Sample', lines=(*common[:-1], common[-1].replace('threshold', 'limit')))
    assert revised['duplicate_of'] == root['id']
    assert revised['duplicate_kind'] == 'content_overlap'
    assert revised['arxiv_id'] is None
    path = f"/api/documents/{root['id']}/work-versions"
    assert client.get(path).json()['latest_readable_version'] is None

    different_title = upload('near-title', heading=f'{title} II', author='Mira Sample')
    different_author = upload('same-title-other-author', author='Hana Fiction')
    assert len({item['work_id'] for item in (root, different_title, different_author)}) == 3

    identified = upload('identified-revision', author='Mira Sample', marker='arXiv: 2611.78912v2')
    assert identified['duplicate_of'] == root['id']
    conflict = upload('different-identifier', author='Mira Sample', marker='arXiv: 2611.78913v1')
    assert conflict['work_id'] not in {root['work_id'], different_author['work_id']}

    same_identifier = upload('more-changes', heading='Synthetic Calibration with a New Protocol',
                             marker='arXiv: 2611.78912v3', lines=('This manuscript records a different protocol.',))
    assert same_identifier['duplicate_of'] == root['id']
    assert same_identifier['duplicate_kind'] == 'same_work_version'
    assert client.get(f"/api/documents/{identified['id']}/work-versions").json()['latest_readable_version'] == 'v3'

    citation = upload('ambiguous-metadata', marker='Related studies include arXiv: 2611.78912v2.')
    assert citation['arxiv_id'] is None
    assert not citation['duplicate_of']
    assert {item['id'] for item in client.get(path).json()['items']} == {
        root['id'], revised['id'], identified['id'], same_identifier['id'],
    }

    response = client.post(f"/api/documents/{revised['id']}/reprocess", json={'ocr': False, 'tables': False})
    assert response.status_code == 200
    reparsed = wait_for_document(client, revised['id'])
    assert reparsed['work_id'] == root['work_id']
    assert reparsed['duplicate_of'] == root['id']


def test_work_default_controls_only_implicit_library_questions(client, tmp_path, monkeypatch):
    documents = []
    for version in (7, 1):
        path = tmp_path / f'version-file-{len(documents)}.pdf'
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), 'Synthetic Quartz Calibration', fontsize=16)
            page.insert_text((72, 100), f'arXiv: 2611.78914v{version}')
            page.insert_text((72, 130), f'Quartz calibration uses {version} oscillators.')
            pdf.save(path)
        doc = wait_for_document(client, upload_pdf(client, path)['id'])
        assert doc['status'] == 'ready'
        documents.append(doc)

    existing, incoming = documents
    existing_id, incoming_id = existing['id'], incoming['id']
    peers = {existing_id, incoming_id}
    # Other tests intentionally leave incomplete records in the shared client.
    list_documents = store.list_documents
    monkeypatch.setattr(store, 'list_documents', lambda: [doc for doc in list_documents() if doc['id'] in peers])
    route = f'/api/documents/{incoming_id}'
    view = client.get(f'{route}/work-versions').json()
    assert view['preferred_document_id'] is None
    assert view['existing_document_id'] == existing_id
    assert view['latest_readable_version'] == 'v7'

    def choice(action, expected):
        return client.patch(f'{route}/work-default', json={
            'action': action, 'expected_document_id': expected,
        })

    def ask(**selection):
        response = client.post('/api/ask', json={
            'question': 'What does quartz calibration use?', 'use_model': False, **selection,
        })
        assert response.status_code == 200, response.text
        record = client.get(f"/api/agent-runs/{response.json()['run_id']}").json()
        return record, set(record['document_ids']) & peers

    assert ask()[1] == peers
    assert choice('keep', existing_id).json() == {'preferred_document_id': existing_id}
    prior, selected = ask()
    assert selected == {existing_id}
    anchor = next(item for item in prior['evidence'] if item['document_id'] == existing_id)
    assert anchor['parse_run_id'] == existing['parse_run_id']

    assert choice('replace', incoming_id).status_code == 409
    assert choice('replace', existing_id).json() == {'preferred_document_id': incoming_id}
    assert choice('replace', existing_id).json() == {'preferred_document_id': incoming_id}
    assert choice('keep', existing_id).status_code == 409
    assert ask()[1] == {incoming_id}
    assert ask(document_ids=[existing_id])[1] == {existing_id}
    assert ask(parse_runs={existing_id: existing['parse_run_id']})[1] == peers
    assert client.get(f'/api/documents/{existing_id}/work-versions').json()['preferred_document_id'] == incoming_id
    library = {item['id']: item['is_work_default'] for item in client.get('/api/documents').json()['items']}
    assert library[existing_id] is False and library[incoming_id] is True

    saved = client.get(f"/api/agent-runs/{prior['run_id']}").json()
    assert anchor in saved['evidence']
    block = client.get(f"/api/documents/{existing_id}/structure", params={
        'page': anchor['page'], 'parse_run_id': anchor['parse_run_id'],
    }).json()
    assert any(item['id'] == anchor['block_id'] for item in block['blocks'])
    for doc in documents:
        assert hashlib.sha256(client.get(f"/api/documents/{doc['id']}/pdf").content).hexdigest() == doc['sha256']

    with monkeypatch.context() as settings:
        settings.setenv('SCHOLARTRACE_DEMO_MODE', 'true')
        settings.setenv('SCHOLARTRACE_DEMO_ALLOW_WRITES', 'false')
        assert choice('keep', incoming_id).status_code == 403

    assert client.delete(route).status_code == 200
    assert choice('keep', incoming_id).status_code == 404
    assert client.get(f'/api/documents/{existing_id}/work-versions').json()['preferred_document_id'] is None
    assert ask()[1] == {existing_id}
