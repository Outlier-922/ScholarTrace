"""Keep scanned grid geometry, candidate data, and QA evidence distinct."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest

from backend.app import ocr, table_refine_scan as scan
from conftest import upload_pdf, wait_for_document


def test_scanned_table_api_preserves_source_and_never_promotes_ocr_to_evidence(client, tmp_path, monkeypatch):
    native = pymupdf.open()
    page = native.new_page(width=400, height=400)
    page.insert_text((45, 56), 'SYNTHETIC raster only', fontsize=16)
    raster = page.get_pixmap()
    image = pymupdf.open()
    scanned = image.new_page(width=400, height=400)
    scanned.insert_image(scanned.rect, pixmap=raster)
    path = tmp_path / 'table-scan.pdf'
    image.save(path)
    native.close()
    image.close()
    doc = wait_for_document(client, upload_pdf(client, path)['id'])
    region = {'document_id': doc['id'], 'parse_run_id': doc['parse_run_id'], 'page': 1,
              'bbox': [.1, .1, .95, .8], 'header_bottom': .25, 'method': 'scan_ocr'}

    def fixture_ocr(rgb):
        assert rgb.shape == (840, 1020, 3)
        # Local crop pixel boxes at 3 pixels per PDF point.
        positions = [(20, 65, 100, 90), (320, 65, 425, 90), (700, 65, 760, 90),
                     (20, 230, 125, 257), (350, 230, 399, 257),
                     (20, 340, 115, 365), (470, 340, 680, 365)]
        return SimpleNamespace(boxes=[np.array([[a,b], [c,b], [c,d], [a,d]]) for a,b,c,d in positions],
                               txts=['Model','Accuracy','Cost','Alpha','28.4','Beta','2 · 1018'],
                               scores=[.97]*7)

    with ThreadPoolExecutor(max_workers=1) as worker:
        monkeypatch.setattr(client.app.state, 'parse_executor', worker)
        monkeypatch.setattr(ocr, '_engine', lambda: fixture_ocr)
        original = client.get('/api/documents/'+doc['id']+'/table-page-image',
                              params={'page': 1, 'parse_run_id': doc['parse_run_id']})
        assert original.content.startswith(b'\x89PNG')
        r = client.post('/api/table-regions', json={**region, 'column_cuts': [.31, .59]})
        assert r.status_code == 200, r.text
        source = r.json()
        assert source['algorithm'] == scan.VERSION and not source['semantic_verified']
        assert source['grid']['rows'] == [['Alpha','28.4',None],['Beta',None,None]]
        assert source['grid']['unassigned'][0]['text'] == '2 · 1018'
        cell = source['grid']['cells'][1]
        assert cell['bbox'] == pytest.approx([v/1200 for v in (470,350,519,377)])
        assert cell['word_ids'] == ['ocr4'] and cell['ocr_confidence'] == .97
        url = '/api/table-regions/'+source['id']
        assert sha256(client.get(url+'/image').content).hexdigest() == source['image_sha256']
        r = client.post(url+'/revisions', json={'labels':['Model','Accuracy','Cost'],
            'rows':[['Alpha','28.4',None],['Beta',None,'2 · 10^18']], 'note':'Synthetic correction'})
        assert r.status_code == 200 and r.json()['semantic_verified'] is False
        assert client.get(url).json()['grid']['rows'][1][2] is None
        r = client.post('/api/ask', json={'question':'Alpha accuracy', 'document_ids':[doc['id']],
            'table_sources':[{'source_id':source['id']}], 'table_only': True, 'use_model': False})
        assert r.status_code == 422 and '仅供原图核对' in r.text

        def unavailable():
            raise ocr.OCRUnavailable('synthetic missing runtime')
        monkeypatch.setattr(ocr, '_engine', unavailable)
        assert client.post('/api/table-regions', json=region).status_code == 503
        monkeypatch.setattr(ocr, '_engine', lambda: lambda rgb: SimpleNamespace(boxes=None, txts=None, scores=None))
        r = client.post('/api/table-regions', json=region)
        assert r.status_code == 422 and '未能从所选区域' in r.text
    assert client.delete('/api/documents/'+doc['id']).status_code == 200


def test_too_few_suggested_columns_leave_separate_numeric_fragments_unassigned():
    def token(i, text, x, y, w):
        return {'id': str(i), 'text': text, 'bbox': [x,y,x+w,y+.02], 'confidence': .99}

    words = [token(0,'Name',.12,.12,.08), token(1,'Values',.55,.12,.09),
             token(2,'Model A',.12,.24,.11), token(3,'30.1',.55,.24,.03),
             token(4,'50.9',.65,.24,.03)]
    grid = scan.build(words, [.1,.1,.9,.6], .2, None, 1.3)
    assert grid['rows'] == [['Model A',None]]
    assert grid['unassigned'][0]['word_ids'] == ['3','4']


def test_ruled_scan_keeps_group_label_as_one_original_cell():
    words = [
        {'id': str(i), 'text': text, 'bbox': [x, y, x + .035, y + .02], 'confidence': .95}
        for i, (text, x, y) in enumerate([
            ('Group', .14, .12), ('Score', .55, .12),
            ('(A)', .14, .31), ('2.7', .56, .27), ('3.1', .56, .36), ('4.2', .56, .48),
        ])
    ]
    grid = scan.build(words, [.1,.1,.9,.6], .2, [.1,.4,.9], 1.3,
                      horizontal=[.21,.43,.53], vertical=[.4])
    assert grid['rows'] == [['(A)','2.7'],[None,'3.1'],[None,'4.2']]
    assert grid['cells'][0]['row_span'] == 2 and grid['cells'][0]['word_ids'] == ['2']
    assert grid['cells'][2]['covered_by'] == [0,0] and grid['cells'][2]['text'] is None
