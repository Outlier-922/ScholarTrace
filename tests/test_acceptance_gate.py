import pytest
from scripts.check_acceptance import assess


def test_incomplete_required_item_blocks_readiness(tmp_path):
    data = {'application_version': 'synthetic', 'items': [{'id': 'required', 'required': True, 'state': 'missing'}]}
    assert assess(data, tmp_path)['submission_ready'] is False


def test_optional_work_is_not_invented_as_organizer_requirement(tmp_path):
    (tmp_path / 'proof.md').write_text('SYNTHETIC evidence')
    data = {'application_version': 'synthetic', 'items': [{'id': 'required', 'required': True, 'state': 'verified', 'proof_paths': ['proof.md']}, {'id': 'optional', 'required': False, 'state': 'missing'}]}
    assert assess(data, tmp_path)['submission_ready'] is True


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
