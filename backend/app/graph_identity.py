"""Conservative local work matching with original PDF title/name witnesses."""
import re
import unicodedata


def norm(text):return ''.join(c for c in unicodedata.normalize('NFKD',text).casefold() if c.isalnum())


def anchor(block):return {k:block[k] for k in ['block_id','page','bbox','text']} if 'block_id' in block else {k:block[k] for k in ['page','bbox','text']}|{'block_id':block['id']}


def native(block):return (block.get('metadata') or {}).get('extraction') in {None,'native','native_pdf'}


def author_name(line):
    if re.search(r'abstract|arxiv|university|institute|department|laborator|research|openai|google|microsoft|published|copyright|https?://|@|\b(?:school|college|faculty|centre|center|affiliation|corporation)\b|\bmeta\s+ai\b',line,re.I):return None
    first=re.split(r',|\band\b|[;|]|[*∗†‡]|\s+\d+|(?<=[^\W\d_])\d+',line,maxsplit=1)[0].strip()
    names=re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*",first)
    if not 2<=len(names)<=6 or not all(n[0].isupper() for n in names) or all(n.isupper() for n in names):return None
    return {'name':' '.join(names),'surname':norm(names[-1]),'raw':first}


def author_email_roster(block):
    if block['type']!='table_candidate' or (block.get('metadata') or {}).get('extraction')!='native_pdf':return False
    lines=[line.strip() for line in block['text'].splitlines() if line.strip()]
    email=lambda text:bool(re.fullmatch(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',text))
    return len(lines)>=4 and bool(author_name(lines[0])) and email(lines[1]) and bool(author_name(lines[2])) and email(lines[3])


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
    if not all(native(b) for b in titles):return None
    if len(norm(title))<14 or len(re.findall(r'\w+',title))<2:return None
    bottom=titles[-1]['bbox'][3]
    for block in blocks[start+len(titles):]:
        box=block['bbox'];line=re.search(r'\S[^\r\n]*',block['text'])
        if not line or not native(block) or box[1]<bottom-.005 or box[1]>min(.6,bottom+.3):continue
        roster=author_email_roster(block)
        if box[3]-box[1]>.12 and not roster:continue
        name=author_name(line.group())
        if not name:continue
        start=line.start();end=start+len(name['raw'])
        return {'title':title,'first_author':name['name'],'surname':name['surname'],'title_sources':[anchor(b) for b in titles],
                'author_source':anchor(block),'author_name_span':{'start':start,'end':end,'text':block['text'][start:end]},
                'author_layout':'name_email_roster' if roster else 'name_row','status':'pdf_literal_identity_candidate'}
    return None


def matches(reference,sources):
    text=norm(reference);words=set(re.findall(r'[^\W\d_]+',unicodedata.normalize('NFKD',reference).casefold()))
    return [s for s in sources if (w:=s.get('identity_witness')) and norm(w['title']) in text and w['surname'] in words]
