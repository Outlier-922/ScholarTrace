"""Conservative purpose cues immediately before an individual citation."""
import re

from . import graph, graph_identity

RULE_VERSION = 'citation-purpose-v3'
SINGLE_MARKER = re.compile(rf'\[\s*(?:\d{{1,4}}|{graph.AUTHOR_KEY_TEXT})\s*\]')
QUALIFIER = re.compile(r'\b(?:not|no|without|never|would|could|future)\b', re.IGNORECASE)
RULES = (
    ('method', 'normalization_step', r'\bfollowed\s+by\s+(?:layer|batch|group)\s+normalization\s*$'),
    ('data', 'named_dataset', r'\b(?:trained|evaluated|tested)\s+on\s+(?:the\s+)?[\w -]{1,50}\s+dataset\s*$'),
    ('comparison', 'comparison_with', r'\bcomparison\s+with\s+[\w -]{1,60}\s*$'),
    ('background', 'further_reading', r'\bfor\s+further\s+reading\s*[,;:]?\s*(?:see|consult)\s*$'),
    ('background', 'general_survey', r'\bfor\s+(?:an?\s+)?(?:general\s+|introductory\s+)(?:survey|overview|introduction)\s+(?:of|to|on)\s+[\w -]{1,50}\s*[,;:]?\s*(?:see|consult)\s*$'),
)


def suggest(block, link):
    """Return an anchored review hint when a supported literal cue is present."""
    if block['type'] != 'paragraph' or not graph_identity.native(block):
        return None
    marker = link['marker_text']
    author_year = link.get('citation_kind') == 'author_year'
    if len(link['reference_ids']) != 1 or not (author_year or SINGLE_MARKER.fullmatch(marker)):
        return None
    text = block['text']
    offset = link['offset']
    end = offset + len(marker)
    if offset < 0 or text[offset:end] != marker:
        return None
    window_start = max(0, offset - 121)
    window = text[window_start:offset]
    if QUALIFIER.search(re.split(r'[.!?;]\s', window)[-1]):
        return None
    if author_year:
        suffix = re.match(r"\s+for\s+an?\s+(?:(?:general|introductory)\s+)?(?:survey|overview|review)\b", text[end:], re.I)
        if not suffix:
            return None
        end += suffix.end()
        quote = text[offset:end]
        if text.count(quote) == 1:
            return {'purpose': 'background', 'rule': 'author_year_overview', 'rule_version': RULE_VERSION,
                    'quote': quote, 'quote_start': offset, 'quote_end': end, 'semantic_verified': False}
        return None
    # Include one extra character so a clipped word cannot satisfy a boundary.
    for purpose, rule, pattern in RULES:
        match = re.search(pattern, window, re.IGNORECASE)
        if not match or (window_start and match.start() == 0):
            continue
        start = window_start + match.start()
        quote = text[start:end]
        if text.count(quote) == 1:
            return {'purpose': purpose, 'rule': rule, 'rule_version': RULE_VERSION,
                    'quote': quote, 'quote_start': start, 'quote_end': end,
                    'semantic_verified': False}
    return None
