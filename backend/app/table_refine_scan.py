"""Rebuild a candidate grid from local OCR inside an explicitly selected crop."""

from __future__ import annotations

import hashlib
import math
import re
from statistics import median

import pymupdf

from . import ocr
from .formula_native import check_pdf
from .table_refine_native import lines

VERSION = 'scan-ocr-grid-v2'
NUMBER = re.compile(r'[+\-−]?\d+(?:[.,]\d+)?%?')
METRIC = re.compile(r'[+\-−]?\d+(?:[.,]\d+)?(?:[%KMB])?', re.I)


def _center(item, axis):
    return (item['bbox'][axis] + item['bbox'][axis + 2]) / 2


def _union(items):
    return [min(i['bbox'][0] for i in items), min(i['bbox'][1] for i in items),
            max(i['bbox'][2] for i in items), max(i['bbox'][3] for i in items)]


def _heading_groups(line, gap):
    groups = []
    for word in line:
        if groups and word['bbox'][0] - max(w['bbox'][2] for w in groups[-1]) <= gap:
            groups[-1].append(word)
        else:
            groups.append([word])
    return groups


def _gutter(left, right, body):
    """Prefer the widest space crossing the fewest OCR boxes within the header gap."""
    middle = (left + right) / 2
    if right - left < .002:
        return middle
    spans = [(max(left, w['bbox'][0]), min(right, w['bbox'][2])) for w in body
             if w['bbox'][0] < right and w['bbox'][2] > left]
    events = {left: 0, right: 0}
    for a, b in spans:
        events[a] = events.get(a, 0) + 1
        events[b] = events.get(b, 0) - 1
    active = 0
    points = sorted(events)
    candidates = []
    for a, b in zip(points, points[1:]):
        active += events[a]
        center = (a + b) / 2
        candidates.append((active, a - b, abs(center - middle), center))
    return min(candidates)[-1]


def _column(box, cuts):
    # An OCR box can overhang the ink a little. Never split a text string
    # based on estimated character offsets when it overlaps two columns.
    c = (box[0] + box[2]) / 2
    for i, (x0, x1) in enumerate(zip(cuts, cuts[1:])):
        if x0 <= c < x1 and box[0] >= x0 - .001 and box[2] <= x1 + .001:
            return i
    return None


def _body_gutters(body, cuts, height, aspect):
    """Split crowded numeric columns only when both alignments recur."""
    body_rows = lines(body, height * .37)
    tolerance = max(.003, height * aspect * .27)
    added = []
    for index, (x0, x1) in enumerate(zip(cuts, cuts[1:])):
        candidates = [[w for w in row if _column(w['bbox'], cuts) == index and METRIC.fullmatch(w['text'])]
                      for row in body_rows]
        full = max(candidates, key=len)
        if len(full) < 2:
            continue
        recurring = [w for w in full if sum(
            any(abs(_center(w, 0) - _center(other, 0)) <= tolerance for other in row)
            for row in candidates
        ) >= 2]
        for a, b in zip(recurring, recurring[1:]):
            left, right = a['bbox'][2], b['bbox'][0]
            if right - left < tolerance or not x0 + .006 < (left + right) / 2 < x1 - .006:
                continue
            cut = _gutter(left, right, body)
            # Never derive a new boundary through intact OCR text.
            if not any(w['bbox'][0] + .001 < cut < w['bbox'][2] - .001 for w in body):
                added.append(cut)
    return added


def _row_groups(body, cuts, horizontal, vertical, height):
    spans = []
    if not any(abs(cuts[1] - line) < .002 for line in vertical):
        return body, spans
    for top, bottom in zip(horizontal, horizontal[1:]):
        band = [w for w in body if top < _center(w, 1) < bottom]
        labels = [w for w in band if _column(w['bbox'], cuts) == 0]
        if len(labels) != 1 or not re.fullmatch(r'\([A-Za-z0-9]+\)', labels[0]['text']):
            continue
        others = [w for w in band if w is not labels[0]]
        if len(lines(others, height * .37)) >= 2:
            spans.append((top, bottom, labels[0]))
    excluded = {w['id'] for _, _, w in spans}
    return [w for w in body if w['id'] not in excluded], spans


def build(words, bbox, header_bottom, cuts, aspect, horizontal=(), vertical=()):
    if not words:
        raise ValueError('scan_no_text')
    heading = [w for w in words if _center(w, 1) < header_bottom]
    body = [w for w in words if _center(w, 1) >= header_bottom]
    if not heading or not body:
        raise ValueError('scan_header_or_body_missing')
    height = median(w['bbox'][3] - w['bbox'][1] for w in words)
    headers = [_heading_groups(row, height * aspect * .23) for row in lines(heading, height * .37)]
    if cuts is None:
        anchors = [_union(group) for group in max(headers, key=len)]
        for row in headers:
            for group in row:
                b = _union(group)
                if b[2] + .002 < min(a[0] for a in anchors) or b[0] - .002 > max(a[2] for a in anchors):
                    anchors.append(b)
        anchors.sort()
        cuts = [bbox[0], *[_gutter(a[2], b[0], body) for a, b in zip(anchors, anchors[1:])], bbox[2]]
        # A mostly borderless table can still have a few geometric separators.
        for rule in vertical:
            if min(abs(rule - cut) for cut in cuts) > .006:
                left = any(w['bbox'][2] < rule for w in body)
                right = any(w['bbox'][0] > rule for w in body)
                if left and right and not any(w['bbox'][0] + .001 < rule < w['bbox'][2] - .001 for w in body):
                    cuts.append(rule)
        cuts.sort()
        if len(anchors) >= 5:
            cuts = sorted([*cuts, *_body_gutters(body, cuts, height, aspect)])
    if not 3 <= len(cuts) <= 41 or any(b - a < .002 for a, b in zip(cuts, cuts[1:])):
        raise ValueError('scan_column_geometry_ambiguous')

    count = len(cuts) - 1
    columns = [{'label': '', 'word_ids': []} for _ in range(count)]
    warnings = ['本机 OCR 候选，尚未核验。未识别的格子保持为空；公式、上下标和数字请对照原图。']
    for row in headers:
        if vertical:
            split = []
            for group in row:
                locations = {_column(w['bbox'], cuts) for w in group}
                if len(locations) > 1 and None not in locations:
                    split.extend([[w for w in group if _column(w['bbox'], cuts) == j] for j in sorted(locations)])
                else:
                    split.append(group)
            row = split
        for group in row:
            b = _union(group)
            column = _column(b, cuts)
            targets = [] if column is None else [column]
            if column is None:
                # A spanning header needs independent labels underneath both
                # touched columns; otherwise leave it in the original crop.
                overlap = [j for j in range(count) if min(b[2], cuts[j + 1]) - max(b[0], cuts[j]) > .002]
                if len(overlap) > 1 and all(any(
                    _center(w, 1) > (b[1] + b[3]) / 2 + height * .55
                    and _column(w['bbox'], cuts) == j for w in heading
                ) for j in overlap):
                    targets = overlap
                elif '部分表头无法可靠归列，请对照原图。' not in warnings:
                    warnings.append('部分表头无法可靠归列，请对照原图。')
            label = ' '.join(w['text'] for w in group).strip()
            for j in targets:
                v = columns[j]
                v['label'] = (' / '.join(filter(None, [v['label'], label])))[:250]
                v['word_ids'].extend(w['id'] for w in group)
    for j, value in enumerate(columns):
        if not value['label']:
            value['label'] = f'未命名列{j+1}'

    body, merged = _row_groups(body, cuts, horizontal, vertical, height)
    row_words = lines(body, height * .37)
    if len(row_words) > 150 or len(row_words) * count > 2000:
        raise ValueError('scan_grid_limit')
    centers = [median(_center(w, 1) for w in row) for row in row_words]
    edges = [header_bottom, *[(a + b) / 2 for a, b in zip(centers, centers[1:])], bbox[3]]
    cells, rows, unassigned = [], [], []
    def hold(row, group, reason):
        unassigned.append({'row': row, 'text': ' '.join(w['text'] for w in group),
                           'word_ids': [w['id'] for w in group], 'bbox': _union(group), 'reason': reason})

    for i, row in enumerate(row_words):
        grouped = [[] for _ in range(count)]
        for w in row:
            j = _column(w['bbox'], cuts)
            if j is None:
                hold(i, [w], 'OCR 片段横跨分界，保留为待核对原文，未复制到多个格子。')
            else:
                grouped[j].append(w)
        values = []
        for j, group in enumerate(grouped):
            if any(NUMBER.fullmatch(a['text']) and NUMBER.fullmatch(b['text'])
                   and b['bbox'][0] - a['bbox'][2] > height * aspect * .4
                   for a, b in zip(group, group[1:])):
                hold(i, group, '同一个建议列内出现分离的数值，请补设列界后重建。')
                group = []
            text = ' '.join(w['text'] for w in group) or None
            confidence = min((w['confidence'] for w in group), default=None)
            values.append(text)
            cells.append({
                'row': i, 'column': j, 'text': text, 'word_ids': [w['id'] for w in group],
                'bbox': _union(group) if group else [cuts[j], edges[i], cuts[j+1], edges[i+1]],
                'row_span': 1, 'covered_by': None, 'ocr_confidence': confidence,
            })
        rows.append(values)
    for top, bottom, label in merged:
        covered = [i for i, center in enumerate(centers) if top < center < bottom]
        first = covered[0]
        rows[first][0] = label['text']
        cells[first * count].update(text=label['text'], word_ids=[label['id']], bbox=label['bbox'],
                                    ocr_confidence=label['confidence'], row_span=len(covered))
        for i in covered[1:]:
            cells[i * count]['covered_by'] = [first, 0]
    if not any(v for row in rows for v in row):
        raise ValueError('scan_no_assigned_text')
    if unassigned:
        warnings.append('归列不明的文字另列待核对；空格保持原样。')
    return {'status': 'candidate', 'columns': columns, 'rows': rows, 'cells': cells,
            'unassigned': unassigned, 'cuts': cuts, 'row_edges': edges, 'warnings': warnings}


def _rules(rgb, pix, scale, width, height):
    import numpy as np

    dark = rgb.max(axis=2) < 135

    def positions(density, threshold):
        hits = np.flatnonzero(density >= threshold)
        groups = np.split(hits, np.flatnonzero(np.diff(hits) > 1) + 1)
        return [float(group.mean()) for group in groups if 0 < len(group) <= 8]

    horizontal = [(pix.y + y + .5) / scale / height for y in positions(dark.mean(axis=1), .83)]
    vertical = [(pix.x + x + .5) / scale / width for x in positions(dark.mean(axis=0), .76)]
    return horizontal, vertical


def extract(path, sha, page_number, bbox, header_bottom, cuts):
    check_pdf(path, sha)
    with pymupdf.open(path) as pdf:
        page = pdf[page_number - 1]
        if page.rotation:
            raise ValueError('rotated_scan_table_not_supported')
        width, height = page.rect.width, page.rect.height
        rect = pymupdf.Rect(bbox[0] * width, bbox[1] * height, bbox[2] * width, bbox[3] * height)
        scale = min(3, 1680 / max(rect.width, rect.height), math.sqrt(1_500_000 / rect.get_area()))
        if min(rect.width, rect.height) * scale < 16:
            raise ValueError('scan_crop_too_small')
        engine = ocr._engine()
        import numpy as np

        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, colorspace=pymupdf.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        output = engine(rgb)
        words = []
        if output.boxes is not None and output.txts is not None and output.scores is not None:
            for polygon, raw, score in zip(output.boxes, output.txts, output.scores):
                text, confidence = str(raw).strip(), float(score)
                if not text or not math.isfinite(confidence) or not .35 <= confidence <= 1 or not np.isfinite(polygon).all():
                    continue
                x0, y0 = polygon.min(axis=0)
                x1, y1 = polygon.max(axis=0)
                # pix.x/y describe the true raster origin, including fractional clip rounding.
                b = [max(bbox[0], (pix.x + float(x0)) / scale / width),
                     max(bbox[1], (pix.y + float(y0)) / scale / height),
                     min(bbox[2], (pix.x + float(x1)) / scale / width),
                     min(bbox[3], (pix.y + float(y1)) / scale / height)]
                if b[2] <= b[0] or b[3] <= b[1]:
                    continue
                if len(words) >= 1500 or len(text) > 1000:
                    raise ValueError('scan_word_limit')
                words.append({'id': f'ocr{len(words)}', 'text': text, 'bbox': b,
                              'confidence': round(confidence, 4)})
        horizontal, vertical = _rules(rgb, pix, scale, width, height)
        grid = build(words, bbox, header_bottom, cuts, height / width, horizontal, vertical)
        png = pix.tobytes('png')
        crop_bbox = [pix.x / scale / width, pix.y / scale / height,
                     (pix.x + pix.width) / scale / width, (pix.y + pix.height) / scale / height]
        return {'words': words, 'grid': grid, 'crop_bbox': crop_bbox,
                'image_sha256': hashlib.sha256(png).hexdigest()}, png
