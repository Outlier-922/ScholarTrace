import pytest
from scripts.check_acceptance import assess


def test_incomplete_required_item_blocks_readiness(tmp_path):
    data = {'application_version': 'synthetic', 'items': [{'id': 'required', 'required': True, 'state': 'missing'}]}
    assert assess(data, tmp_path)['submission_ready'] is False


def test_optional_work_is_not_invented_as_organizer_requirement(tmp_path):
    (tmp_path / 'proof.md').write_text('SYNTHETIC evidence')
    data = {'application_version': 'synthetic', 'items': [{'id': 'required', 'required': True, 'state': 'verified', 'proof_paths': ['proof.md']}, {'id': 'optional', 'required': False, 'state': 'missing'}]}
    assert assess(data, tmp_path)['submission_ready'] is True


def test_user_managed_delivery_does_not_block_product_completion(tmp_path):
    (tmp_path/'proof.md').write_text('Synthetic functional verification')
    data={'application_version':'synthetic','items':[
        {'id':'feature','required':True,'state':'verified','proof_paths':['proof.md']},
        {'id':'video','required':True,'state':'partial','completion_scope':'user_handoff'}]}
    result=assess(data,tmp_path)
    assert result['product_ready'] and not result['unfinished_product_items']
    assert not result['submission_ready'] and result['unfinished_required_items']==['video']


def test_empty_or_unscoped_list_cannot_become_ready(tmp_path):
    for items in ([], [{'id': 'optional', 'required': False, 'state': 'missing'}]):
        with pytest.raises(ValueError, match='nonempty_required_scope'):
            assess({'application_version': 'synthetic', 'items': items}, tmp_path)


def test_verified_claim_needs_existing_evidence(tmp_path):
    data = {'application_version': 'synthetic', 'items': [{'id': 'required', 'required': True, 'state': 'verified', 'proof_paths': ['proof.md']}]}
    with pytest.raises(ValueError, match='missing_acceptance_evidence'):
        assess(data, tmp_path)
    (tmp_path / 'proof.md').write_text('SYNTHETIC evidence')
    assert assess(data, tmp_path)['submission_ready'] is True
