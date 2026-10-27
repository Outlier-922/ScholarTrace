"""Pure evaluation checks. Source coordinates are not semantic entailment."""
from __future__ import annotations

import hashlib
import json
import math
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def work_alias(value):
    value = value.casefold().strip().removeprefix('https://arxiv.org/abs/').removeprefix('arxiv:')
    return re.sub(r'v\d+$', '', value)


def validate_pack(pack):
    sources = pack['sources']
    if len({case['id'] for case in pack['cases']}) != len(pack['cases']):
        raise ValueError('duplicate_case_id')
    ownership = {}
    for key, source in sources.items():
        if source['split'] not in {'development', 'holdout'}:
            raise ValueError('invalid_split')
        identities = [source['work'], source['sha256'],
                      re.sub(r'\W+', '', source['title'].casefold()), *source.get('aliases', [])]
        for identity in identities:
            alias = work_alias(identity)
            if alias in ownership and ownership[alias] != source['split']:
                raise ValueError('work_leakage:' + key)
            ownership[alias] = source['split']
    for case in pack['cases']:
        if not case['sources'] or not case['gold_groups']:
            raise ValueError('empty_sources_or_gold')
        if {sources[key]['split'] for key in case['sources']} != {case['split']}:
            raise ValueError('case_crosses_split:' + case['id'])
        for group in case['gold_groups']:
            if not group or any(anchor['source'] not in case['sources'] for anchor in group):
                raise ValueError('invalid_gold_scope')
            if any(not anchor['text_sha256'] or len(anchor['bbox']) != 4 for anchor in group):
                raise ValueError('invalid_gold_anchor')


def bind_sources(pack, documents, get_structure):
    """Bind by exact PDF digest/version; remap UUIDs only when gold anchors match."""
    bound = {}
    for key, source in pack['sources'].items():
        matches = [item for item in documents if item['sha256'] == source['sha256']]
        if len(matches) != 1:
            raise ValueError('source_sha_missing_or_ambiguous:' + key)
        document = matches[0]
        if document.get('version_label') != source.get('version_label'):
            raise ValueError('source_version_changed:' + key)
        structure = get_structure(document['id'], document['parse_run_id'])
        blocks = {block['id']: block for block in structure['blocks']}
        expected = [anchor for case in pack['cases'] for group in case['gold_groups']
                    for anchor in group if anchor['source'] == key]
        for anchor in expected:
            if not any(anchor_matches(anchor, block) for block in blocks.values()):
                raise ValueError('gold_anchor_changed:' + key)
        bound[key] = {'document': document, 'parse_run_id': structure['parse_run_id'], 'blocks': blocks}
    return bound


def anchor_matches(anchor, block):
    return (anchor['page'] == block.get('page') and anchor['bbox'] == block.get('bbox')
            and anchor['text_sha256'] == text_hash(block.get('text', '')))


def request_body(case, bound, live):
    """Only the question and selected scope go to /ask. Gold never goes to it."""
    selected = [bound[key] for key in case['sources']]
    return {'question': case['question'], 'document_ids': [b['document']['id'] for b in selected],
            'parse_runs': {b['document']['id']: b['parse_run_id'] for b in selected},
            'limit': 6, 'use_model': live}


def score_response(case, response, bound):
    scope = {bound[key]['document']['id']: (key, bound[key]) for key in case['sources']}
    evidence = response.get('evidence', [])
    valid, resolved = [], []
    seen_ids = set()
    for item in evidence:
        source, binding = scope.get(item.get('document_id'), (None, {}))
        block = binding.get('blocks', {}).get(item.get('block_id'))
        ok = bool(block and item.get('id') and item['id'] not in seen_ids
                  and item.get('parse_run_id') == binding['parse_run_id']
                  and item.get('page') == block['page'] and item.get('bbox') == block['bbox']
                  and item.get('text') == block['text'])
        seen_ids.add(item.get('id'))
        valid.append(ok)
        if ok:
            resolved.append((source, block))
    hits = [any(anchor['source'] == source and anchor_matches(anchor, block)
                for anchor in group for source, block in resolved) for group in case['gold_groups']]
    ids = {item['id'] for item, ok in zip(evidence, valid) if ok}
    claims = response.get('claims', [])
    covered = sum(bool(claim.get('evidence_ids')) and set(claim['evidence_ids']) <= ids for claim in claims)
    return {'evidence_count': len(evidence), 'valid_anchor_count': sum(valid),
            'gold_groups': len(hits), 'gold_groups_retrieved': sum(hits),
            'claim_count': len(claims), 'claims_with_valid_source_ids': covered,
            'mode': response.get('mode'), 'retrieval_mode': response.get('retrieval', {}).get('mode'),
            'semantic_correctness': None}


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def summarize(rows, reviews=None):
    reviews = reviews or []
    if len({entry['case_id'] for entry in reviews}) != len(reviews):
        raise ValueError('duplicate_review')
    by_id = {entry['case_id']: entry for entry in reviews}
    reviewed = []
    for row in rows:
        review = by_id.get(row['case_id'], {})
        if review.get('status') != 'reviewed':
            continue
        if (not review.get('reviewer') or review.get('response_sha256') != row.get('response_sha256')
                or review.get('case_sha256') != row.get('case_sha256')):
            raise ValueError('stale_or_unattributed_review')
        if review.get('judgment') not in {'correct', 'partial', 'incorrect', 'abstain', 'error'}:
            raise ValueError('invalid_review_judgment')
        if review['judgment'] in {'correct','partial','incorrect'} and not (
                row.get('live_model_requested') and row.get('score',{}).get('mode') == 'llm'
                and row['score']['claim_count'] > 0):
            raise ValueError('cannot_grade_missing_answer')
        reviewed.append((row, review))
    total = len(rows)
    successful = [row for row in rows if row.get('score')]
    scores = [row['score'] for row in successful]
    evidence_count = sum(score['evidence_count'] for score in scores)
    gold_count = sum(row['gold_groups'] for row in rows)
    claim_count = sum(score['claim_count'] for score in scores)
    times = [row['elapsed_seconds'] for row in rows]
    complete = bool(total and len(reviewed) == total)
    generated = [row for row in successful if row['score']['mode'] == 'llm']
    answered = sum(row['score']['claim_count'] > 0 for row in generated)
    correct = sum(review['judgment'] == 'correct' for _, review in reviewed)
    semantic_ready = complete and all(row.get('live_model_requested') for row in rows)
    claim_reviews = [(row, review) for row, review in reviewed if row.get('score', {}).get('claim_count')]
    support_complete = (semantic_ready and claim_count > 0
                        and all(isinstance(review.get('supported_claims'), int)
                                and 0 <= review['supported_claims'] <= row['score']['claim_count']
                                for row, review in claim_reviews))
    return {'case_count': total, 'request_errors': total - len(successful),
            'evidence_anchor_validity': sum(s['valid_anchor_count'] for s in scores) / evidence_count if evidence_count else None,
            'evidence_anchor_denominator': evidence_count,
            'gold_group_recall_at_6': sum(s['gold_groups_retrieved'] for s in scores) / gold_count if gold_count else None,
            'gold_group_denominator': gold_count,
            'claim_source_id_coverage': sum(s['claims_with_valid_source_ids'] for s in scores) / claim_count if claim_count else None,
            'claim_denominator': claim_count, 'generated_case_count': len(generated),
            'answered_case_count': answered, 'abstained_case_count': len(generated) - answered,
            'model_fallback_count': sum(row.get('live_model_requested', False) and row['score']['mode'] != 'llm' for row in successful),
            'reviewed_cases': len(reviewed), 'review_coverage': len(reviewed) / total if total else None,
            'fully_correct_over_all_cases': correct / total if semantic_ready else None,
            'fully_correct_over_answered_cases': correct / answered if semantic_ready and answered else None,
            'citation_semantic_support': sum(review['supported_claims'] for _, review in claim_reviews) / claim_count if support_complete else None,
            'latency_seconds': {'n': len(times), 'p50': percentile(times, .5), 'p95': percentile(times, .95),
                                'method': 'nearest-rank; sequential HTTP plus local validation incl. errors; not concurrent-load performance'},
            'quality_notice': 'Gold and answer review remain pending unless explicitly attributed. Coordinate/ID checks do not establish semantic correctness.'}
