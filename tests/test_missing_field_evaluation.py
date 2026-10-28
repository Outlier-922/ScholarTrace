"""A retrieval fallback is not a model refusal or a quality pass."""
from scripts.evaluate_missing_fields import disposition


def test_generated_empty_result_remains_ungraded():
    result=disposition({'mode':'llm','claims':[]})
    assert result['no_retained_factual_claims'] and result['semantic_correctness'] is None
    assert result['empty_result_origin']=='unknown_model_or_validation_filter'


def test_retrieval_only_is_not_refusal():
    result=disposition({'mode':'evidence_only','claims':[]})
    assert result['no_retained_factual_claims'] is None and not result['generated']


def test_retained_claims_do_not_become_a_correct_answer():
    result=disposition({'mode':'llm','claims':[{'text':'Synthetic ungraded statement'}]})
    assert result['retained_claims']==1 and result['no_retained_factual_claims'] is False
    assert result['semantic_correctness'] is None
