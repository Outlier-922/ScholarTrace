"""Protect verified visual regions while keeping nearby prose in translation."""
from collections import defaultdict
import re

import pymupdf

TABLE_CAPTION = re.compile(r"^\s*(?:Table|Tab\.?|表)\s*\d+(?:\.\d+)*\s*[:：.．]", re.I)


def inside(box, region, padding=.003):
    return (box[0] >= region[0] - padding and box[1] >= region[1] - padding
            and box[2] <= region[2] + padding and box[3] <= region[3] + padding)


def _ruled_regions(page, captions):
    """Use aligned rules adjacent to a numbered caption as a bounded region."""
    width, height = page.rect.width, page.rect.height
    rules = set()
    for drawing in page.get_drawings():
        rect = drawing['rect']
        if rect.width >= width * .18 and rect.height <= height * .003:
            rules.add(tuple(round(value, 5) for value in (
                rect.x0 / width, rect.y0 / height, rect.x1 / width
            )))
    rules = sorted(rules, key=lambda rule: rule[1])
    for caption in captions:
        left, _, right, bottom = caption
        starts = [rule for rule in rules if -.002 <= rule[1] - bottom <= .035
                  and min(right, rule[2]) - max(left, rule[0]) >= .7 * (rule[2] - rule[0])]
        if not starts:
            continue
        start = starts[0]
        stop = min((box[1] for box in captions if box[1] > bottom), default=1)
        aligned = [rule for rule in rules if start[1] <= rule[1] < min(stop, start[1] + .55)
                   and abs(rule[0] - start[0]) <= .012 and abs(rule[2] - start[2]) <= .012]
        if len(aligned) >= 3 and aligned[-1][1] - start[1] >= .025:
            yield [start[0], start[1], start[2], aligned[-1][1]]


def _vertical_labels(page):
    width, height = page.rect.width, page.rect.height
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    for block in page.get_text('dict', flags=flags)['blocks']:
        lines = block.get('lines', [])
        if lines and all(abs(line['dir'][0]) <= .2 for line in lines):
            x0, y0, x1, y1 = block['bbox']
            yield [x0 / width, y0 / height, x1 / width, y1 / height]


def visual_blocks(path, blocks):
    by_page = defaultdict(list)
    for block in blocks:
        by_page[block['page']].append(block)
    result = {}
    with pymupdf.open(path) as pdf:
        for number, items in by_page.items():
            tables = [b['bbox'] for b in items if b['type'] == 'table']
            captions = [b['bbox'] for b in items if b['type'] == 'table_caption' and TABLE_CAPTION.match(b['text'])]
            table_captions = [box for box in captions if not any(
                -.002 <= table[1] - box[3] <= .035 for table in tables
            )]
            has_figures = any(b['type'] == 'figure_caption' for b in items)
            narrow = [b for b in items if b['type'] in {'paragraph', 'heading'}
                      and b.get('metadata', {}).get('extraction') == 'native_pdf'
                      and len(b['text']) <= 45 and b['bbox'][2] - b['bbox'][0] <= .05]
            page = pdf[number - 1]
            if not page.rotation and table_captions:
                tables.extend(_ruled_regions(page, table_captions))
            for block in items:
                if block['type'] not in {'figure_caption', 'table_caption'} and any(
                    inside(block['bbox'], table, .012) for table in tables
                ):
                    result[block['id']] = '原表区文字原样保留'
            if not page.rotation and has_figures and len(narrow) >= 4:
                labels = list(_vertical_labels(page))
                if len(labels) >= 4:
                    for block in narrow:
                        if any(inside(block['bbox'], box, .002) for box in labels):
                            result[block['id']] = '图中竖排标签原样保留'
    return result


def is_prose(text):
    plain = ' '.join(text.split())
    return (bool(re.search(r'[.!?:。！？：][\s\]\)"”’]*$', plain))
            and (len(re.findall(r'\b[A-Za-z]{2,}\b', plain)) >= 12
                 or len(re.findall(r'[\u4e00-\u9fff]', plain)) >= 24))


def is_display_math(text):
    plain = ' '.join(text.split())
    return (len(re.findall(r'\b[A-Za-z]{2,}\b', plain)) <= 5
            and (re.search(r'[=∑∏√∂∇∈×]|·\s*(?:min|max|log)\s*\(', plain)
                 or re.fullmatch(r'[)\]]+[A-Za-z\W\d]{1,18}', plain)))
