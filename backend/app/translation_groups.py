"""Conservative two-page semantic units; original blocks stay immutable."""
import re


def prose(block):
    text=block.get('text','')
    return (block.get('type')=='paragraph' and len(text)>=60
            and block.get('metadata',{}).get('extraction')=='native_pdf'
            and len(re.findall(r'(?m)^\s*[-+]?\d+(?:\.\d+)?\s*$',text))<2)


def pairs(all_blocks,selected_ids,*,preserved_ids=()):
    pages={}
    for block in all_blocks:pages.setdefault(block['page'],[]).append(block)
    preserved = set(preserved_ids)
    result=[];used=set()
    for page,blocks in pages.items():
        lefts=[b for b in blocks if prose(b) and b['id'] not in preserved
               and not (b['bbox'][1] >= .84 and re.match(r'^\s*\d{1,2}\s*(?=[A-Z])', b['text']))]
        rights=[b for b in pages.get(page+1,[]) if prose(b) and b['id'] not in preserved]
        if not lefts or not rights:continue
        left,right=lefts[-1],rights[0]
        if left['id'] in used or right['id'] in used:continue
        if not {left['id'],right['id']}<=selected_ids:continue
        if not re.match(r'^\s*[a-z]',right['text']):continue
        a,b=left.get('bbox',[]),right.get('bbox',[])
        if len(a)!=4 or len(b)!=4 or a[3]<.7 or abs(a[0]-b[0])>.12 or abs(a[2]-b[2])>.12:continue
        before=pages[page+1][:pages[page+1].index(right)]
        if any(item.get('type')=='heading' and re.match(r'^\s*\d+(?:\.\d+)*\s',item['text']) for item in before):continue
        article_tail = bool(re.search(r'\b(?:as|using|with|of|on|for|to)\s+(?:a|an|the)\s*$',left['text'],re.I))
        if not (article_tail and b[1] <= .5):
            same_section = bool(left.get('section_path') and left['section_path'] == right.get('section_path'))
            aligned = a[3] >= .84 and abs(a[0]-b[0]) <= .045 and abs(a[2]-b[2]) <= .045
            figure_first = any(item['type'] in {'figure_caption','table_caption'} for item in before)
            nearby = b[1] <= .26 or figure_first and b[1] <= .56
            unfinished = not re.search(r'[.!?:;。！？][)"”’\]]*\s*$',left['text'])
            if not (same_section and aligned and nearby and unfinished):
                continue
        if len(left['text'])+len(right['text'])+1>12000:continue
        result.append((left,right));used.update([left['id'],right['id']])
    return result


def combined(blocks):
    return {**blocks[0],'text':' '.join(block['text'].strip() for block in blocks),
            '_translation_group':[block['id'] for block in blocks]}
