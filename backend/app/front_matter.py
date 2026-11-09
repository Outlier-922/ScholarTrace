"""Read author and affiliation candidates from native first-page evidence."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .graph_identity import author_name, native, norm

Block = dict[str, Any]
NAME_WORD = r"[^\W\d_]+(?:['’\-][^\W\d_]+)*\.?"
NAME_LINE = re.compile(rf"(?P<name>{NAME_WORD}(?: +{NAME_WORD}){{1,5}})(?P<marks>[ *∗†‡\d,]*)")
EMAIL_LINE = re.compile(r"(?:[\w.+\-]+|\{[^{}\s]{1,180}\})@[\w.\-]+\.[a-z]{2,}", re.I)
INSTITUTION = re.compile(
    r"\b(?:universit\w*|institut\w*|department|labs?|laborator\w*|research|"
    r"college|academy|school|centre|center|brain|ai language)\b|大学|学院|实验室|研究[院所]", re.I,
)
NUMBERED_INSTITUTION = re.compile(r"(?P<marker>[1-9]\d?)\s*(?P<name>[^\W\d_][^@]{1,100})")
WORK_NOTE = re.compile(r"(?P<marker>[†‡])\s*Work (?:was )?performed while at .+", re.I)
SUPERSCRIPT = re.compile(r"\^\{([ *∗†‡,\d]+)\}")
BYLINE_NAME = re.compile(rf" *(?P<name>{NAME_WORD}(?: +{NAME_WORD}){{1,5}})(?P<marks>(?: *{SUPERSCRIPT.pattern})+)")
NAME_PARTICLE = re.compile(r"(?<= )(?:da|de|del|di|dos|la|van|von) +")
FOOTNOTE_CUE = re.compile(r"equal contribution|correspon(?:-\s*)?dence to:", re.I)
FOOTNOTE_NUMBER = re.compile(r"(?<!\w)([1-9]\d?)(?=[A-Z])")
FOOTNOTE_END = re.compile(r"\s+(?:work per(?:-|formed)|correspon(?:-\s*)?dence to:)", re.I)


@dataclass(frozen=True)
class Line:
    block: Block
    text: str
    start: int

    def source(self, start: int = 0, end: int | None = None) -> dict[str, Any]:
        end = len(self.text) if end is None else end
        return {
            'document_id': self.block['document_id'], 'parse_run_id': self.block['parse_run_id'],
            'page': self.block['page'], 'block_id': self.block['id'],
            'start': self.start + start, 'end': self.start + end, 'text': self.text[start:end],
        }


def _lines(block: Block) -> list[Line]:
    return [
        Line(block, match.group().strip(), match.start() + len(match.group()) - len(match.group().lstrip()))
        for match in re.finditer(r'[^\r\n]+', block['text']) if match.group().strip()
    ]


def _title_end(blocks: list[Block], title: str) -> float | None:
    target = norm(title)
    if not target:
        return None
    for index, block in enumerate(blocks):
        text = norm(block['text'])
        if block['type'] not in {'title', 'heading'} or not text or not target.startswith(text):
            continue
        bottom = block['bbox'][3]
        for following in blocks[index + 1:index + 4]:
            if text == target:
                return bottom
            if following['type'] not in {'title', 'heading'} or not 0 <= following['bbox'][1] - bottom <= .04:
                break
            text += norm(following['text'])
            bottom = following['bbox'][3]
        if text == target:
            return bottom
    return None


def _name(line: Line) -> re.Match[str] | None:
    match = NAME_LINE.fullmatch(line.text)
    return match if match and author_name(match['name']) else None


def _same_column(name: Line, contact: Block) -> bool:
    left, _, right, bottom = name.block['bbox']
    other_left, top, other_right, _ = contact['bbox']
    overlap = min(right, other_right) - max(left, other_left)
    return (name.block is not contact and -.004 <= top - bottom <= .023
            and overlap >= .65 * min(right - left, other_right - other_left)
            and abs(left + right - other_left - other_right) <= .10)


def _marked_names(block: Block) -> list[tuple[Line, list[str]]]:
    metadata = block.get('metadata') or {}
    layout = metadata.get('layout_text', '')
    raw = block['text']
    if (metadata.get('layout_format') != 'superscript_braces_v1'
            or metadata.get('raw_text_sha256') != hashlib.sha256(raw.encode()).hexdigest()
            or SUPERSCRIPT.sub(r'\1', layout) != raw):
        return []
    names = []
    raw_offset = 0
    for row in layout.split('\n'):
        end = 0
        for match in BYLINE_NAME.finditer(row):
            name = match['name']
            markers = re.findall(r'[1-9]\d?', match['marks'])
            if match.start() != end or not markers or not author_name(NAME_PARTICLE.sub('', name)):
                return []
            start = raw_offset + match.start('name') - match.start()
            names.append((Line(block, name, start), list(dict.fromkeys(markers))))
            raw_offset += len(SUPERSCRIPT.sub(r'\1', match.group()))
            end = match.end()
        if row[end:].strip():
            return []
        raw_offset += len(row) - end + 1
    return names


def _numbered_footnotes(blocks: list[Block]) -> dict[str, list[dict[str, Any]]]:
    footer = [block for block in blocks if .70 <= block['bbox'][1] <= .91
              and block['bbox'][3] - block['bbox'][1] <= .16 and len(block['text']) <= 1400]
    cues = [block for block in footer if FOOTNOTE_CUE.search(block['text'])]
    definitions: dict[str, list[dict[str, Any]]] = {}
    for block in footer:
        left, top, right, _ = block['bbox']
        if not any(abs(top - cue['bbox'][1]) <= .12
                   and min(right, cue['bbox'][2]) - max(left, cue['bbox'][0]) >= .65 * (right - left)
                   for cue in cues):
            continue
        raw = block['text']
        labels = list(FOOTNOTE_NUMBER.finditer(raw))
        for index, label in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(raw)
            value = FOOTNOTE_END.split(raw[label.end():end], maxsplit=1)[0].rstrip(' \r\n,;.')
            if not 2 <= len(value) <= 260 or '@' in value:
                continue
            definitions.setdefault(label[1], []).append(Line(block, value, label.end()).source())
    return definitions


def extract(document: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    blocks = [block for block in structure['blocks']
              if block['page'] == 1 and block['text'] and native(block) and block['bbox'][0] >= .075]
    result = {'document_id': document['id'], 'parse_run_id': structure['parse_run_id'], 'authors': []}
    title_end = _title_end(blocks, document['title'])
    if title_end is None:
        return result
    stop = min((block['bbox'][1] for block in blocks
                if block['bbox'][1] >= title_end and norm(block['text']) in {'abstract', '摘要'}),
               default=title_end + .23)
    header = [block for block in blocks if title_end - .004 <= block['bbox'][1] < min(stop, .65)
              and block['bbox'][3] <= stop + .004]
    header_lines = [_lines(block) for block in header]
    standalone = [lines[0] for lines in header_lines if len(lines) == 1 and _name(lines[0])]
    definitions = [
        (line, match) for lines in header_lines for line in lines
        if (match := NUMBERED_INSTITUTION.fullmatch(line.text))
        and (match.start('name') == match.end('marker') or INSTITUTION.search(match['name']))
    ]
    notes = [(line, match) for block in blocks if block['bbox'][1] >= .70 for line in _lines(block)
             if (match := WORK_NOTE.fullmatch(line.text))]
    authors: dict[tuple[str, int], dict[str, Any]] = {}

    for block, lines in zip(header, header_lines):
        contact_count = sum(bool(EMAIL_LINE.fullmatch(row.text)) for row in lines)
        group: list[Line] = []
        for line in lines:
            if not EMAIL_LINE.fullmatch(line.text):
                group.append(line)
                continue
            # An email delineates a printed contact group. Its domain is never
            # used to derive an affiliation.
            names = [(row, _name(row)) for row in group]
            organizations = []
            while names and names[-1][1] is None and INSTITUTION.search(names[-1][0].text):
                organizations.append(names.pop()[0])
            if not names:
                adjacent = [row for row in standalone if _same_column(row, block)]
                if len(adjacent) == 1:
                    names = [(adjacent[0], _name(adjacent[0]))]
            group = []
            if not names or any(match is None for _, match in names):
                continue
            mailbox_count = line.text.split('}', 1)[0].count(',') + 1 if line.text.startswith('{') else 1
            if len(names) != mailbox_count:
                continue
            for row, match in names:
                assert match is not None
                if not organizations and not match['marks'].strip() and contact_count < 2:
                    continue
                marks = set(re.findall(r'[1-9]\d?|[†‡]', match['marks']))
                affiliations = [{'source': organization.source(),
                                 'method': 'same_contact' if row.block is block else 'adjacent_contact'}
                                for organization in reversed(organizations)]
                for marker in sorted(marks):
                    matches = [(definition, item) for definition, item in definitions
                               if item['marker'] == marker and definition.block['bbox'][1] >= block['bbox'][3] - .004]
                    if len(matches) == 1:
                        definition, item = matches[0]
                        affiliations.append({'source': definition.source(*item.span('name')),
                                             'method': 'numbered_marker'})
                work_notes = [footnote.source() for footnote, item in notes if item['marker'] in marks
                              and sum(other['marker'] == item['marker'] for _, other in notes) == 1]
                source = row.source(*match.span('name'))
                authors.setdefault((source['block_id'], source['start']), {
                    'source': source, 'affiliations': affiliations, 'work_notes': work_notes,
                    '_order': (round(row.block['bbox'][1], 2), row.block['bbox'][0], row.start),
                })
    # Marked bylines can be read independently of the contact layout. Require
    # a verbatim reconstruction of the native text before trusting the marks.
    numbered = [pair for block in header for pair in _marked_names(block)]
    if numbered:
        footnotes = _numbered_footnotes(blocks)
        for line, markers in numbered:
            linked = []
            for marker in markers:
                candidates = [definition.source(*match.span('name')) for definition, match in definitions
                              if match['marker'] == marker and definition.block['bbox'][1] >= line.block['bbox'][3] - .004]
                candidates.extend(footnotes.get(marker, []))
                if len(candidates) == 1:
                    linked.append({'source': candidates[0], 'method': 'numbered_marker'})
            if linked:
                source = line.source()
                authors.setdefault((source['block_id'], source['start']), {
                    'source': source, 'affiliations': linked, 'work_notes': [],
                    '_order': (round(line.block['bbox'][1], 2), line.block['bbox'][0], line.start),
                })
    result['authors'] = sorted(authors.values(), key=lambda item: item['_order'])
    for item in result['authors']:
        item.pop('_order')
    return result
