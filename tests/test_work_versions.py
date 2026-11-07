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
