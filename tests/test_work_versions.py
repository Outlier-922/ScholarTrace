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
