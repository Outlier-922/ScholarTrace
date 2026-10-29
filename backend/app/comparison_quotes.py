"""Literal quote binding for comparison candidates, never semantic entailment."""
import re

VERSION = 'comparison-literal-quotes-v1'


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def span(needle, text):
    """Map whitespace-normalized matching back to original Unicode offsets."""
    chars, starts, ends = [], [], []
    for match in re.finditer(r'\s+|[^\s]', text):
        chars.append(' ' if match.group().isspace() else match.group())
        starts.append(match.start()); ends.append(match.end())
    haystack = ''.join(chars)
    needle = normalized(needle)
    start = haystack.find(needle)
    if not needle or start < 0:
        return None, 'phrase_not_in_quote'
    if haystack.find(needle, start + 1) >= 0:
        return None, 'phrase_not_unique'
    return [starts[start], ends[start + len(needle) - 1]], None


def check(proposal, evidence, validate):
    value, quote = proposal.get('value'), proposal.get('support_quote')
    conditions, ids = proposal.get('conditions', ''), proposal.get('evidence_ids')
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000
            or not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 1200
            or not isinstance(conditions, str) or len(conditions) > 1000):
        return None, 'invalid_fields'
    if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str):
        return None, 'one_quote_source_required'
    source = next((item for item in evidence if item['id'] == ids[0]), None)
    if not source:
        return None, 'source_not_in_cell'
    quote_range, reason = span(quote, source['text'])
    if reason:
        return None, 'quote_' + reason
    raw_quote = source['text'][slice(*quote_range)]
    value_range, reason = span(value, raw_quote)
    if reason:
        return None, 'value_' + reason
    condition_range = None
    if conditions.strip():
        condition_range, reason = span(conditions, raw_quote)
        if reason:
            return None, 'conditions_' + reason
    # Check only the attached quote, without inherited block layout metadata.
    checked = validate({'claims': [{'text': value + ' ' + conditions, 'evidence_ids': ids}]},
                       [{'id': source['id'], 'text': raw_quote}])
    if not checked:
        return None, 'quote_numeric_or_formula_check_rejected'
    absolute = lambda bounds: [quote_range[0] + bounds[0], quote_range[0] + bounds[1]]
    return {'status': 'quote_bound_candidate', 'model_review': 'literal_quote_bound',
            'value': raw_quote[slice(*value_range)], 'conditions': raw_quote[slice(*condition_range)] if condition_range else '',
            'support_quote': raw_quote, 'evidence_ids': ids,
            'quote_binding': {'version': VERSION, 'evidence_id': source['id'], 'quote_range': quote_range,
                              'value_range': absolute(value_range),
                              'conditions_range': absolute(condition_range) if condition_range else None},
            'semantic_verified': False}, None
