"""Literal author-year links for bibliography entries with a unique surname and year."""

import re
from collections import defaultdict

from . import graph_identity, reading

_YEAR = re.compile(r"(?<![\d.])((?:19|20)\d{2})([a-z]?)\b(?!\.\d)", re.I)
_BARE_ROSTER = re.compile(r"^(?P<names>[\w\s.,'’\-]+?)\.\s+(?:19|20)\d{2}[a-z]?\.\s*$")


def repair_date_units(units):
    """Join a bare author/date line to its indented title fragment without swallowing the next author."""
    result = []
    for unit in units:
        previous = result[-1] if result else None
        roster = _BARE_ROSTER.fullmatch(previous['text']) if previous and not (previous.get('marker') or previous.get('author_key')) else None
        if (not roster or len(previous['sources']) != 1 or unit.get('marker') or unit.get('author_key')
                or len(unit['sources']) != len(unit['reference_ids'])
                or not graph_identity.author_name(roster['names']) or ',' not in roster['names']):
            result.append(unit)
            continue
        author = previous['sources'][0]
        if author.get('extraction') not in {'native', 'native_pdf'}:
            result.append(unit)
            continue
        left, _, _, bottom = author['bbox']
        limit = 0
        for source in unit['sources'][:3]:
            x, y, _, edge = source['bbox']
            if (source['page'] != author['page'] or not .008 <= x - left <= .07
                    or not -.003 <= y - bottom <= .015 or source.get('extraction') not in {'native', 'native_pdf'}):
                break
            bottom = edge
            limit += 1
        if not limit:
            result.append(unit)
            continue
        parts = [source['text'].strip() for source in unit['sources']]
        result[-1] = {**previous, 'text': '\n'.join([previous['text'], *parts[:limit]]),
            'sources': [*previous['sources'], *unit['sources'][:limit]],
            'reference_ids': [*previous['reference_ids'], *unit['reference_ids'][:limit]]}
        if limit < len(parts):
            remaining = unit['reference_ids'][limit:]
            result.append({**unit, 'id': 'bib_' + remaining[0], 'text': '\n'.join(parts[limit:]),
                'sources': unit['sources'][limit:], 'reference_ids': remaining})
    return result


def backlinks(structure, units, selected):
    signatures = defaultdict(list)
    printed = {}
    excluded = {source['block_id'] for unit in units for source in unit['sources']}
    for unit in units:
        text = ' '.join(unit['text'][:700].split())
        year = _YEAR.search(text)
        author = graph_identity.author_name(text[:year.start()].lstrip('(').strip()) if year else None
        if author:
            key = (author['surname'], int(year[1]))
            signatures[key].append(unit['id'])
            printed[unit['id']] = key

    results = {}
    for unit_id, surname in selected:
        key = printed.get(unit_id)
        if not key or key[0] != surname or signatures[key] != [unit_id]:
            continue
        _, year = key
        name = re.escape(surname)
        date = rf"{year}[a-z]?\b"
        author_part = rf"\b{name}(?:\s+et\s+al\.|\s+(?:and|&)\s+[A-Za-z][\w'-]+)?"
        # A candidate with an unexplained author-date variant gets no automatic exclusion.
        marker = re.compile(rf"{author_part}\s*(?:\(\s*{date}\s*\)|,\s*{year}[a-z]?\b)", re.I)
        result = []
        for block in structure['blocks']:
            if block['id'] in excluded or not graph_identity.native(block):
                continue
            for match in marker.finditer(block['text']):
                link = reading.citation_anchor(block, match, structure['parse_run_id'], [unit_id])
                kind = 'author_year' if re.search(rf"\b{year}\b", match[0]) else 'author_year_variant'
                result.append({**link, 'citation_kind': kind})
        results[unit_id] = result
    return results
