"""Literal identity clues for a bibliography unit, never identity certification."""
import re
import unicodedata
from . import graph

RULE_VERSION='literal-identity-clues-v1'

def words(text):
    text=re.sub(r'(?<=[A-Za-z])-\r?\n[ \t]*(?=[a-z])','',text)
    text=''.join(c for c in unicodedata.normalize('NFKD',text).casefold() if not unicodedata.combining(c))
    return re.findall(r'[^\W_]+',text)

def clues(text,item):
    source_ids=graph.identifiers(text)
    candidate_ids=[]
    for value in (item.get('identifier'),item.get('doi')):
        if value:candidate_ids.extend(graph.identifiers(value))
    candidate_ids=list(dict.fromkeys(candidate_ids))
    # Normalize arXiv DOI aliases before comparing explicit revision suffixes.
    def arxiv(raw):return raw.removeprefix('10.48550/arxiv.')
    matches=[];version_conflicts=[]
    for root,raw in source_ids:
        candidates=[candidate for other,candidate in candidate_ids if root==other]
        if not candidates:continue
        if root.startswith('arxiv:'):
            original=arxiv(raw)
            if re.search(r'v\d+$',original):
                if original not in {arxiv(c) for c in candidates}:version_conflicts.append(raw);continue
            elif any(r==root and re.search(r'v\d+$',arxiv(v)) for r,v in source_ids):continue
        matches.append(raw)
    matches=list(dict.fromkeys(matches));version_conflicts=list(dict.fromkeys(version_conflicts))
    comparable=any(s.split(':',1)[0]==c.split(':',1)[0] for s,_ in source_ids for c,_ in candidate_ids)
    identifier='multiple_literals' if matches and (version_conflicts or len({r for r,_ in source_ids})>1) else 'matched' if matches else 'revision_differs' if version_conflicts else 'not_aligned' if comparable else 'unavailable'
    tokens=words(text);title=words(item.get('title') or '')
    title_found=any(tokens[i:i+len(title)]==title for i in range(len(tokens)-len(title)+1)) if len(title)>=2 else None
    first=words((item.get('authors') or [''])[0]);surname=first[-1] if first and len(first[-1])>=2 else None
    author_found=surname in tokens if surname else None
    return {'rule_version':RULE_VERSION,'identifier':identifier,'matched_literals':matches,'revision_conflicts':version_conflicts,
        'source_identifiers':[raw for _,raw in source_ids],'candidate_identifier':item.get('identifier'),
        'title_literal_match':title_found,'first_author_surname':surname,'first_author_surname_match':author_found,
        'semantic_verified':False}
