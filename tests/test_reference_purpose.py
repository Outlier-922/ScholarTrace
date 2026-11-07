import pytest

from backend.app.reference_purpose import suggest


@pytest.mark.parametrize(('text', 'marker', 'purpose'), [
    ('The residual step is followed by layer\nnormalization [1].', '[1]', 'method'),
    ('The model is evaluated on the Example dataset [ABC+19].', '[ABC+19]', 'data'),
    ('We report a comparison with baseline A [2].', '[2]', 'comparison'),
])
def test_hint_points_to_literal_span_with_citation(text, marker, purpose):
    block = {'text': text, 'type': 'paragraph'}
    link = {'marker_text': marker, 'offset': text.index(marker), 'reference_ids': ['ref']}
    hint = suggest(block, link)
    assert hint['purpose'] == purpose and hint['semantic_verified'] is False
    assert text[hint['quote_start']:hint['quote_end']] == hint['quote']
    assert hint['quote'].endswith(marker)


@pytest.mark.parametrize(('text', 'marker', 'kind', 'metadata'), [
    ('Dyer et al. (2016) [8]\n91.7', '[8]', 'table_candidate', {}),
    ('followed by layer normalization [1,2]', '[1,2]', 'paragraph', {}),
    ('This is not followed by layer normalization [1].', '[1]', 'paragraph', {}),
    ('followed by layer normalization [1]', '[1]', 'paragraph', {'extraction': 'rapidocr'}),
    ('followed by layer normalization [1]. followed by layer normalization [1].', '[1]', 'paragraph', {}),
])
def test_ambiguous_or_unreliable_positions_abstain(text, marker, kind, metadata):
    assert suggest({'text': text, 'type': kind, 'metadata': metadata},
                   {'marker_text': marker, 'offset': text.index(marker), 'reference_ids': ['ref']}) is None
