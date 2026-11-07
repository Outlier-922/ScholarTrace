from backend.app.reference_identity import clues

def test_pdf_line_break_accents_and_exact_doi_are_separate_clues():
    result=clues('José García. Neural network gram-\nmars. doi:10.1234/example.',
                 {'title':'Neural Network Grammars','authors':['Jose Garcia'],'identifier':'10.1234/example'})
    assert result['identifier']=='matched' and result['matched_literals']==['10.1234/example']
    assert result['title_literal_match'] is True and result['first_author_surname_match'] is True
    assert result['semantic_verified'] is False

def test_token_prefix_and_surname_substring_do_not_count_as_literal_matches():
    result=clues('Alice Blacksmith. Neural network grammars extensions.',
                 {'title':'Neural network grammar','authors':['Jane Smith'],'identifier':'10.1234/example'})
    assert result['title_literal_match'] is False and result['first_author_surname_match'] is False
    assert result['identifier']=='unavailable'

def test_doi_alias_does_not_hide_explicit_arxiv_version_conflict():
    result=clues('arXiv:2605.22222v7; doi:10.48550/arxiv.2605.22222',{'title':'A Test','authors':[],'identifier':'2605.22222v1','doi':'10.48550/arxiv.2605.22222'})
    assert result['identifier']=='revision_differs' and not result['matched_literals']

def test_unversioned_alias_does_not_create_conflict_when_explicit_version_matches():
    result=clues('arXiv:2605.22222v7',{'title':'A Test','authors':[],'identifier':'2605.22222v7','doi':'10.48550/arxiv.2605.22222'})
    assert result['identifier']=='matched' and not result['revision_conflicts']

def test_several_literal_ids_do_not_look_like_unambiguous_identity():
    result=clues('See doi:10.1234/first and doi:10.1234/second.',{'identifier':'10.1234/first','title':'First Work','authors':[]})
    assert result['identifier']=='multiple_literals' and result['first_author_surname_match'] is None
