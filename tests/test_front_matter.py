import hashlib

from backend.app import front_matter, store


def test_front_matter_pins_author_institution_and_work_note_to_the_requested_parse(client):
    doc = store.create_document('synthetic-header.pdf', '', store.new_id('sha'), 1)
    store.update_document(doc['id'], title='Synthetic Metadata Study', status='ready')
    run = doc['parse_run_id']
    rows = [
        ('title', 'Synthetic Metadata Study', [.20, .10, .80, .13]),
        ('heading', 'Mira Example∗†', [.20, .18, .36, .194]),
        ('paragraph', 'Sample University\nmira@example.test', [.18, .196, .38, .23]),
        ('paragraph', 'Alex Fixture 2\nalex@example.test', [.58, .18, .82, .23]),
        ('paragraph', '2Fixture Institute', [.50, .242, .78, .26]),
        ('heading', 'Abstract', [.40, .30, .60, .32]),
        ('paragraph', 'Fictional Person\nFictional University\nx@example.test', [.19, .35, .80, .42]),
        ('paragraph', '†Work performed while at Other Institute.', [.19, .84, .78, .86]),
    ]
    blocks = [
        {'id': store.new_id('block'), 'type': kind, 'text': text, 'bbox': bbox, 'order': index,
         'metadata': {'extraction': 'native_pdf'}}
        for index, (kind, text, bbox) in enumerate(rows)
    ]
    store.save_page(doc['id'], run, 1, 600, 800, 0, [0, 0, 600, 800], 'ready', blocks)
    store.set_run_status(run, 'ready')

    route = f"/api/documents/{doc['id']}/front-matter"
    result = client.get(route).json()
    assert result['parse_run_id'] == run
    mira, alex = result['authors']
    assert [item['source']['text'] for item in (mira, alex)] == ['Mira Example', 'Alex Fixture']
    assert [item['method'] for item in mira['affiliations']] == ['adjacent_contact']
    assert [item['method'] for item in alex['affiliations']] == ['numbered_marker']
    assert mira['affiliations'][0]['source']['text'] == 'Sample University'
    assert alex['affiliations'][0]['source']['text'] == 'Fixture Institute'
    assert mira['work_notes'][0]['text'] == '†Work performed while at Other Institute.'
    assert not alex['work_notes']
    by_id = {item['id']: item['text'] for item in blocks}
    for author in result['authors']:
        anchors = [author['source'], *author['work_notes'], *(item['source'] for item in author['affiliations'])]
        for anchor in anchors:
            assert (anchor['document_id'], anchor['parse_run_id'], anchor['page']) == (doc['id'], run, 1)
            assert by_id[anchor['block_id']][anchor['start']:anchor['end']] == anchor['text']

    newer = store.start_reprocess(doc['id'], {'ocr': True, 'tables': False})
    store.save_page(doc['id'], newer['parse_run_id'], 1, 600, 800, 0, [0, 0, 600, 800], 'ocr_ready', [
        {**block, 'id': store.new_id('block'), 'metadata': {'extraction': 'rapidocr'}} for block in blocks
    ])
    store.update_document(doc['id'], status='ready')
    store.set_run_status(newer['parse_run_id'], 'ready')
    assert client.get(route).json()['authors'] == []
    assert client.get(route, params={'parse_run_id': run}).json() == result
    assert client.get(route, params={'parse_run_id': 'missing_run'}).status_code == 404
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200


def test_native_marked_byline_requires_verbatim_layout_and_unique_affiliation_labels():
    raw = 'Mira de Example * 1 2 Alex Fixture 2'
    rows = [
        ('title', 'Synthetic Numbered Byline', [.2, .10, .8, .13]),
        ('paragraph', raw, [.18, .17, .82, .19]),
        ('heading', 'Abstract', [.4, .22, .6, .24]),
        ('paragraph', '*Equal contribution 1Example University\n2Fixture Institute. Work per-\nformed elsewhere.',
         [.08, .78, .46, .83]),
    ]
    blocks = [{'id': f'block-{index}', 'type': kind, 'text': text, 'bbox': bbox,
               'document_id': 'doc', 'parse_run_id': 'run', 'page': 1, 'metadata': {}}
              for index, (kind, text, bbox) in enumerate(rows)]
    layout = blocks[1]['metadata'] = {
        'layout_format': 'superscript_braces_v1',
        'raw_text_sha256': hashlib.sha256(raw.encode()).hexdigest(),
        'layout_text': 'Mira de Example ^{* 1 2} Alex Fixture ^{2}',
    }
    document = {'id': 'doc', 'title': rows[0][1]}
    structure = {'parse_run_id': 'run', 'blocks': blocks}
    authors = front_matter.extract(document, structure)['authors']
    assert [author['source']['text'] for author in authors] == ['Mira de Example', 'Alex Fixture']
    assert [[a['source']['text'] for a in author['affiliations']] for author in authors] == [
        ['Example University', 'Fixture Institute'], ['Fixture Institute'],
    ]
    for author in authors:
        for source in [author['source'], *(a['source'] for a in author['affiliations'])]:
            block = next(block for block in blocks if block['id'] == source['block_id'])
            assert block['text'][source['start']:source['end']] == source['text']

    blocks.append({**blocks[-1], 'id': 'conflicting-footnote', 'text': '2Other Institute'})
    authors = front_matter.extract(document, structure)['authors']
    assert [author['source']['text'] for author in authors] == ['Mira de Example']
    assert [a['source']['text'] for a in authors[0]['affiliations']] == ['Example University']
    layout['layout_text'] = layout['layout_text'].replace('Fixture', 'Forged')
    assert front_matter.extract(document, structure)['authors'] == []


def test_contact_block_with_an_organization_and_multiple_mailboxes_does_not_create_a_person(client):
    doc = store.create_document('synthetic-group.pdf', '', store.new_id('sha'), 1)
    store.update_document(doc['id'], title='Synthetic Group Contacts', status='ready')
    blocks = [
        {'id': store.new_id('block'), 'type': 'title', 'text': 'Synthetic Group Contacts',
         'bbox': [.2, .1, .8, .13], 'order': 0},
        {'id': store.new_id('block'), 'type': 'paragraph', 'text': 'Fictional Collective\n{alice,bob}@example.test',
         'bbox': [.3, .18, .7, .21], 'order': 1},
    ]
    store.save_page(doc['id'], doc['parse_run_id'], 1, 600, 800, 0, [0, 0, 600, 800], 'ready', blocks)
    store.set_run_status(doc['parse_run_id'], 'ready')
    assert client.get(f"/api/documents/{doc['id']}/front-matter").json()['authors'] == []
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
