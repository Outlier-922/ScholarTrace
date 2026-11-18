"""Native-word grid candidates in an explicitly selected PDF region. No LLM/OCR."""
import hashlib
import math
import re
import statistics
from pathlib import Path
import pymupdf

VERSION='native-header-grid-v4'


def lines(words,tolerance):
    result=[]
    for word in sorted(words,key=lambda w:((w['bbox'][1]+w['bbox'][3])/2,w['bbox'][0])):
        center=(word['bbox'][1]+word['bbox'][3])/2
        if result and abs(center-statistics.median((v['bbox'][1]+v['bbox'][3])/2 for v in result[-1]))<=tolerance:result[-1].append(word)
        else:result.append([word])
    return [sorted(row,key=lambda w:w['bbox'][0]) for row in result]


def header_groups(words,tolerance):
    result=[]
    for row in lines(words,tolerance):
        groups=[]
        for word in row:
            if groups and word['bbox'][0]-groups[-1]['x1']<=tolerance*1.8:
                groups[-1]['x1']=max(groups[-1]['x1'],word['bbox'][2]);groups[-1]['words'].append(word)
            else:groups.append({'x0':word['bbox'][0],'x1':word['bbox'][2],'words':[word]})
        result.append(groups)
    return result


def horizontal_merges(legacy, cuts, row_words, top, bottom):
    """Use only ruled cell geometry with explicit covered slots and matching source words."""
    if legacy.get('strategy') not in {'lines', 'lines_strict'}:
        return {}
    by_slot = {(cell['row'], cell['column']): cell for cell in legacy.get('cells', [])}
    merges = {}
    for (row, column), cell in by_slot.items():
        box, text = cell.get('bbox'), cell.get('text')
        if not box or not text or '\n' in text or box[1] < top - 1e-6 or box[3] > bottom + 1e-6:
            continue
        left_edges = [i for i, cut in enumerate(cuts) if abs(box[0] - cut) < .002]
        right_edges = [i for i, cut in enumerate(cuts) if abs(box[2] - cut) < .002]
        if len(left_edges) != 1 or len(right_edges) != 1:
            continue
        first, end = left_edges[0], right_edges[0]
        width = end - first
        if width < 2 or not all(
            (covered := by_slot.get((row, column + shift))) is not None
            and covered.get('bbox') is None and covered.get('text') is None
            for shift in range(1, width)
        ):
            continue
        fragments = [
            (i, [word for word in words
                 if box[0] <= (word['bbox'][0] + word['bbox'][2]) / 2 < box[2]
                 and box[1] <= (word['bbox'][1] + word['bbox'][3]) / 2 < box[3]])
            for i, words in enumerate(row_words)
        ]
        occupied = [(i, words) for i, words in fragments if words]
        if len(occupied) != 1 or ' '.join(word['text'] for word in occupied[0][1]) != text:
            continue
        index, words = occupied[0]
        if any(cuts[first] <= (word['bbox'][0] + word['bbox'][2]) / 2 < cuts[end]
               and word not in words for word in row_words[index]):
            continue
        merges.setdefault(index, []).append((first, end, box, words))
    return {
        row: entries for row, entries in merges.items()
        if sum(end - first for first, end, _, _ in entries)
        == len({col for first, end, _, _ in entries for col in range(first, end)})
    }


def text_header_spans(groups, cuts, tolerance):
    """Recognize a centered parent heading above separate printed child headings."""
    spans = []
    for row_index, row in enumerate(groups[:-1]):
        for group in row:
            first = next((i for i, right in enumerate(cuts[1:]) if group['x0'] < right), None)
            end = next((i for i, right in enumerate(cuts[1:], 1) if group['x1'] <= right), None)
            if first is None or end is None or end - first < 2:
                continue
            center = (group['x0'] + group['x1']) / 2
            if abs(center - (cuts[first] + cuts[end]) / 2) > tolerance * 2.5:
                continue
            lower = next((line for line in groups[row_index + 1:]
                          if any(cuts[first] < (child['x0'] + child['x1']) / 2 < cuts[end]
                                 for child in line)), [])
            if not all(any(cuts[j] <= child['x0'] < child['x1'] <= cuts[j+1] for child in lower)
                       for j in range(first, end)):
                continue
            words = group['words']
            spans.append({'first_column':first, 'col_span':end-first,
                          'bbox':[group['x0'], min(w['bbox'][1] for w in words),
                                  group['x1'], max(w['bbox'][3] for w in words)],
                          'text':' '.join(word['layout_text'] for word in words),
                          'word_ids':[word['id'] for word in words]})
    return spans


def text_body_spans(row_words, cuts, header_spans, tolerance):
    """A centered, single printed value can span a confirmed group of leaf columns."""
    result = {}
    for index, row in enumerate(row_words):
        groups = header_groups(row, tolerance)[0]
        for span in header_spans:
            first, end = span['first_column'], span['first_column'] + span['col_span']
            left, right = cuts[first], cuts[end]
            in_group = [g for g in groups if left <= (g['x0'] + g['x1']) / 2 < right]
            if len(in_group) != 1:
                continue
            group = in_group[0]
            center = (group['x0'] + group['x1']) / 2
            if (not left <= group['x0'] < group['x1'] <= right
                    or abs(center - (left + right) / 2) > tolerance * 3.25
                    or not any(group['x0'] + .0004 < cut < group['x1'] - .0004 for cut in cuts[first+1:end])):
                continue
            words = group['words']
            value = ' '.join(word['layout_text'] for word in words)
            if not re.fullmatch(r'[-−+]?\d+(?:\.\d+)?\s*[·×]\s*10\s*(?:\^\{?[-−]?\d+\}?|\d+)', value):
                continue
            box = [group['x0'], min(w['bbox'][1] for w in words),
                   group['x1'], max(w['bbox'][3] for w in words)]
            if any(first < previous_end and end > previous_first
                   for previous_first, previous_end, _, _ in result.get(index, [])):
                continue
            result.setdefault(index, []).append((first, end, box, words))
    return result


def header_boundary(legacy, box):
    cells = legacy.get('cells', [])
    top = [cell for cell in cells if cell['row'] == 0 and cell.get('bbox')]
    bottom = max((cell['bbox'][3] for cell in top), default=box[1] + (box[3] - box[1]) * .15)
    if legacy.get('strategy') not in {'lines', 'lines_strict'}:
        return bottom
    children = [cell for cell in cells if cell['row'] == 1 and cell.get('bbox')]
    groups = []
    for cell in top:
        left, _, right, edge = cell['bbox']
        labels = [
            child['text'] for child in children
            if abs(child['bbox'][1] - edge) < 1e-5 and left - 1e-5 <= child['bbox'][0]
            and child['bbox'][2] <= right + 1e-5 and child.get('text')
        ]
        if len(labels) >= 2 and all(any(char.isalpha() for char in label) for label in labels):
            groups.append({' '.join(label.casefold().split()) for label in labels})
    # A repeated label under separate parent groups is evidence of a second heading row.
    if any(a & b for index, a in enumerate(groups) for b in groups[index + 1:]):
        return max(bottom, *(child['bbox'][3] for child in children))
    return bottom


def build(words,bbox,header_bottom,legacy,cuts=None):
    if not words:return {'status':'manual_only','columns':[],'rows':[],'cells':[],'unassigned':[],'warnings':['所选区域没有原生文字；没有执行OCR或猜测表格。']}
    height=statistics.median(w['bbox'][3]-w['bbox'][1] for w in words)
    tolerance=height*.35
    heading=[w for w in words if (w['bbox'][1]+w['bbox'][3])/2<header_bottom]
    body=[w for w in words if w not in heading]
    groups=header_groups(heading,tolerance)
    if not heading or not body:raise ValueError('header_or_body_missing')
    if not cuts:
        anchors=list(max(groups,key=len))
        for row in groups:
            for g in row:
                if g['x1']<min(a['x0'] for a in anchors) or g['x0']>max(a['x1'] for a in anchors):anchors.append(g)
        for cell in legacy.get('cells',[]):
            rect=cell.get('bbox')
            if cell['row']==0 and rect and not any(rect[0]<=(w['bbox'][0]+w['bbox'][2])/2<=rect[2] for w in heading):
                anchors.append({'x0':rect[0],'x1':rect[2],'words':[]})
        anchors.sort(key=lambda a:a['x0'])
        edges={v for c in legacy.get('cells',[]) if c.get('bbox') for v in (c['bbox'][0],c['bbox'][2])}
        cuts=[bbox[0]]
        for left,right in zip(anchors,anchors[1:]):
            gap=(left['x1']+right['x0'])/2
            allowed=[e for e in edges if left['x1']-1e-6<=e<=right['x0']+1e-6]
            cuts.append(min(allowed,key=lambda e:abs(e-gap)) if allowed else gap)
        cuts.append(bbox[2])
        boundaries=sorted(edges)
        # Keep refinements that recover more physical columns than the original grid.
        if (
            legacy.get('strategy') in {'lines', 'lines_strict'}
            and len(cuts) <= len(boundaries) == legacy.get('col_count', 0) + 1
            and len(boundaries) >= 3 and abs(boundaries[0]-bbox[0]) < .002
            and abs(boundaries[-1]-bbox[2]) < .002
        ):
            cuts=[bbox[0], *boundaries[1:-1], bbox[2]]
    if not 3<=len(cuts)<=41 or any(b-a<.002 for a,b in zip(cuts,cuts[1:])):raise ValueError('column_geometry_ambiguous')
    header_merges = horizontal_merges(legacy, cuts, lines(heading, tolerance), bbox[1], header_bottom)
    header_spans = [
        {'first_column': first, 'col_span': end-first, 'bbox': box,
         'text': ' '.join(word['layout_text'] for word in source), 'word_ids': [word['id'] for word in source]}
        for entries in header_merges.values() for first, end, box, source in entries
    ]
    text_spans = text_header_spans(groups, cuts, tolerance) if legacy.get('strategy') not in {'lines', 'lines_strict'} else []
    header_spans.extend(text_spans)
    header_by_word = {key: index for index, span in enumerate(header_spans) for key in span['word_ids']}
    columns=[]
    for column,(x0,x1) in enumerate(zip(cuts,cuts[1:])):
        parts=[];refs=[]
        for row in groups:
            for group in row:
                # A parent header can cover several leaf columns; keep its actual words.
                known = {header_by_word.get(w['id']) for w in group['words']}
                if len(known) == 1 and None not in known:
                    span = header_spans[next(iter(known))]
                    if span['first_column'] <= column < span['first_column'] + span['col_span'] and span['text'] not in parts:
                        parts.append(span['text'])
                        refs.extend(span['word_ids'])
                    continue
                parent=False
                bottom=max(w['bbox'][3] for w in group['words'])
                for k,cut in enumerate(cuts[1:-1],start=1):
                    if column in {k-1,k} and group['x0']<cut<group['x1']:
                        below=[any(cuts[q]<=(w['bbox'][0]+w['bbox'][2])/2<cuts[q+1] and (w['bbox'][1]+w['bbox'][3])/2>bottom for w in heading) for q in (k-1,k)]
                        parent=parent or all(below)
                if parent or group['x0']<=(x0+x1)/2<=group['x1'] or any(x0<=(w['bbox'][0]+w['bbox'][2])/2<x1 for w in group['words']):
                    text=' '.join(w['layout_text'] for w in group['words'])
                    if text not in parts:parts.append(text);refs.extend(w['id'] for w in group['words'])
        columns.append({'label':' / '.join(parts) or f'未命名列{column+1}','word_ids':refs})
    spanning=[];skip=set()
    for cell in legacy.get('cells',[]):
        rect=cell.get('bbox');text=cell.get('text')
        if not rect or not text or '\n' in text or rect[1]<header_bottom or rect[3]-rect[1]<height*1.7:continue
        matches=[j for j in range(len(columns)) if abs(rect[0]-cuts[j])<.003 and abs(rect[2]-cuts[j+1])<.003]
        contained=[w for w in body if rect[0]<=(w['bbox'][0]+w['bbox'][2])/2<=rect[2] and rect[1]<=(w['bbox'][1]+w['bbox'][3])/2<=rect[3]]
        if len(matches)==1 and ' '.join(w['text'] for w in contained)==text:
            spanning.append((matches[0],rect,contained));skip.update(w['id'] for w in contained)
    row_words=lines([w for w in body if w['id'] not in skip],tolerance)
    if not row_words or len(row_words)>150 or len(row_words)*len(columns)>2000:raise ValueError('table_grid_limit')
    merged = horizontal_merges(legacy, cuts, row_words, header_bottom, bbox[3])
    if text_spans:
        merged.update(text_body_spans(row_words, cuts, text_spans, tolerance))
    centers=[statistics.median((w['bbox'][1]+w['bbox'][3])/2 for w in row) for row in row_words]
    ys=[header_bottom]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[bbox[3]]
    rows=[[None]*len(columns) for _ in row_words];cells=[];unassigned=[]
    for i,row in enumerate(row_words):
        row_merges = merged.get(i, [])
        merged_words = {word['id'] for _, _, _, words in row_merges for word in words}
        row = [word for word in row if word['id'] not in merged_words]
        crossing=[w for w in row if any(w['bbox'][0]+.0003<x<w['bbox'][2]-.0003 for x in cuts[1:-1])]
        for left,right in zip(row,row[1:]):
            connected=(re.search(r'[·×+*/=]$',left['text']) or re.match(r'^[·×+*/=]',right['text'])) and right['bbox'][0]-left['bbox'][2]<height*.8
            if connected and any((left['bbox'][0]+left['bbox'][2])/2<x<(right['bbox'][0]+right['bbox'][2])/2 for x in cuts[1:-1]):crossing.extend([left,right])
        if crossing:
            unassigned.append({'row':i,'text':' '.join(w['layout_text'] for w in row),'word_ids':[w['id'] for w in row],'reason':'文字或相连表达式跨越建议列界，整行保留待核对，未按列赋值。'})
        for j in range(len(columns)):
            selected=[] if crossing else [w for w in row if cuts[j]<=(w['bbox'][0]+w['bbox'][2])/2<cuts[j+1]]
            text=' '.join(w['layout_text'] for w in selected) if selected else None
            rows[i][j]=text
            cells.append({'row':i,'column':j,'text':text,'word_ids':[w['id'] for w in selected],'bbox':[cuts[j],ys[i],cuts[j+1],ys[i+1]],'row_span':1,'covered_by':None})
        for first, end, rect, words in row_merges:
            offset = i * len(columns)
            text = ' '.join(word['layout_text'] for word in words)
            rows[i][first] = text
            cells[offset + first].update(text=text, word_ids=[word['id'] for word in words],
                                         bbox=rect, col_span=end - first)
            for col in range(first + 1, end):
                cells[offset + col]['covered_by'] = [i, first]
    for col,rect,selected in spanning:
        indexes=[i for i,y in enumerate(centers) if rect[1]<=y<=rect[3]]
        if len(indexes)<2:continue
        if any(cells[i*len(columns)+col].get('col_span', 1) > 1
               or cells[i*len(columns)+col]['covered_by'] for i in indexes):
            continue
        start=indexes[0];cell=cells[start*len(columns)+col]
        cell.update(text=' '.join(w['layout_text'] for w in selected),word_ids=[w['id'] for w in selected],bbox=rect,row_span=len(indexes))
        rows[start][col]=cell['text']
        for i in indexes[1:]:cells[i*len(columns)+col]['covered_by']=[start,col]
    assigned={w for cell in cells for w in cell['word_ids']}|{w for item in unassigned for w in item['word_ids']}
    leftovers=[w for w in body if w['id'] not in assigned]
    if leftovers:unassigned.append({'row':0,'text':' '.join(w['layout_text'] for w in leftovers),'word_ids':[w['id'] for w in leftovers],'reason':'源标签未能绑定到跨行网格，保留原文字与坐标待人工核对。'})
    warnings = ['坐标重建是未核验候选；没有将空白自动填为上一行值。','表头路径来自可见文字位置，不能据此认定指标语义、单位或实验条件。']
    if merged:
        warnings.append('跨列格保留一个原始值和整体坐标。覆盖列留空，跨列值不作为单列问答证据。')
    return {'status':'candidate','columns':columns,'header_spans':header_spans,'rows':rows,'cells':cells,'unassigned':unassigned,'cuts':cuts,'row_edges':ys,'warnings':warnings}


def extract(path,sha,page_number,bbox,header_bottom,legacy,cuts):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha:raise ValueError('pdf_hash_changed')
    with pymupdf.open(path) as pdf:
        page=pdf[page_number-1]
        if page.rotation:raise ValueError('rotated_table_region_not_supported')
        width,height=page.rect.width,page.rect.height
        if max(width,height)>20000:raise ValueError('table_page_limit')
        rect=pymupdf.Rect(bbox[0]*width,bbox[1]*height,bbox[2]*width,bbox[3]*height)
        raw_words=page.get_text('words',clip=rect)
        if len(raw_words)>2000:raise ValueError('table_word_limit')
        spans=[s for b in page.get_text('dict',clip=rect,flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks'] for line in b.get('lines',[]) for s in line['spans'] if s['flags']&1]
        words=[]
        for raw in raw_words:
            x0,y0,x1,y1,text=raw[:5];layout=text
            for span in spans:
                s=pymupdf.Rect(span['bbox']);sup=span['text'].strip()
                if sup and text.endswith(sup) and s.intersects(pymupdf.Rect(raw[:4])) and s.x0>=x0-.5 and s.x1<=x1+.5:
                    layout=text[:-len(sup)]+'^{'+sup+'}';break
            words.append({'id':f'w{len(words)}','text':text,'layout_text':layout,'bbox':[x0/width,y0/height,x1/width,y1/height]})
            if len(words)>2000:raise ValueError('table_word_limit')
        grid=build(words,bbox,header_bottom,legacy,cuts)
        crop=(rect+(-4,-4,4,4))&page.rect
        scale=min(2,math.sqrt(3_000_000/max(1,crop.width*crop.height)))
        png=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=crop,alpha=False).tobytes('png')
        return {'words':words,'grid':grid,'crop_bbox':[crop.x0/width,crop.y0/height,crop.x1/width,crop.y1/height],'image_sha256':hashlib.sha256(png).hexdigest()},png
