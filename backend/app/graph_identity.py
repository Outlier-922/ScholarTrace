"""Conservative local work matching with original PDF title/name witnesses."""
import re
import unicodedata


def norm(text):return ''.join(c for c in unicodedata.normalize('NFKD',text).casefold() if c.isalnum())


def anchor(block):return {k:block[k] for k in ['block_id','page','bbox','text']} if 'block_id' in block else {k:block[k] for k in ['page','bbox','text']}|{'block_id':block['id']}


def pdf_identity(document,structure):
    blocks=[b for b in structure['blocks'] if b['page']==1]
    start=next((i for i,b in enumerate(blocks) if b['type']=='title' and norm(b['text'])==norm(document['title'])),None)
    if start is None:return None
    titles=[blocks[start]];font=titles[0].get('metadata',{}).get('font_size',0)
    for block in blocks[start+1:start+4]:
        previous=titles[-1];size=block.get('metadata',{}).get('font_size',0)
        overlap=min(previous['bbox'][2],block['bbox'][2])-max(previous['bbox'][0],block['bbox'][0])
        if not font or abs(size-font)>.15 or not 0<=block['bbox'][1]-previous['bbox'][3]<=.04 or overlap<=0:break
        titles.append(block)
    title=' '.join(b['text'] for b in titles)
    if len(norm(title))<14 or len(re.findall(r'\w+',title))<2:return None
    bottom=titles[-1]['bbox'][3]
    for block in blocks[start+len(titles):]:
        box=block['bbox'];line=block['text'].splitlines()[0] if block['text'].strip() else ''
        if box[1]<bottom-.005 or box[1]>min(.6,bottom+.3) or box[3]-box[1]>.12:continue
        if re.search(r'abstract|arxiv|university|institute|department|laborator|research|openai|google|microsoft|published|copyright|https?://|@',line,re.I):continue
        first=re.split(r',|\band\b|[;|]',line)[0]
        names=re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*",first)
        if not 2<=len(names)<=6 or not all(n[0].isupper() for n in names) or all(n.isupper() for n in names):continue
        return {'title':title,'first_author':' '.join(names),'surname':norm(names[-1]),'title_sources':[anchor(b) for b in titles],'author_source':anchor(block),'status':'pdf_literal_identity_candidate'}
    return None


def matches(reference,sources):
    text=norm(reference);words=set(re.findall(r'[^\W\d_]+',unicodedata.normalize('NFKD',reference).casefold()))
    return [s for s in sources if (w:=s.get('identity_witness')) and norm(w['title']) in text and w['surname'] in words]
