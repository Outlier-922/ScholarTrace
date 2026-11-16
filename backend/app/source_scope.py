"""Validate explicit historical ParseRuns without silently following reprocessing."""
import re
from fastapi import HTTPException
from . import store


def pin(documents, parse_runs):
    identities={document['id'] for document in documents}
    if set(parse_runs)-identities:
        raise HTTPException(422,'解析版本包含未选文献')
    result=[]
    for document in documents:
        run=parse_runs.get(document['id'],document['parse_run_id'])
        if not store.get_parse_run(document['id'],run):
            raise HTTPException(404,'选定解析版本不存在')
        result.append({**document,'parse_run_id':run,'_retrieval_run':run})
    return result


def reference_block_ids(structure):
    """Bound bibliography spans even when a year line is mislabelled heading."""
    blocked=set();inside=False
    for block in structure.get('blocks',[]):
        text=re.sub(r'\s+',' ',block['text']).strip()
        if block['type']=='heading':
            if re.fullmatch(r'(?:\d+[.\s]*)?(references|bibliography|参考文献)',text,re.I):
                inside=True;blocked.add(block['id']);continue
            if inside and (block.get('heading_level') == 1 and block.get('metadata',{}).get('extraction') == 'rapidocr'
                           or re.match(r'appendix|附录|attention visualizations|chapter\s+\d+|part\s+\d+|第.{1,8}章',text,re.I)
                           or re.match(r'^[A-Z](?:\.\d+)?\s+(?:Additional|Details|Experiments|Illustrations|Comparison)',text)
                           or re.match(r'^\d{1,2}(?:\.\d+)*[.\s]+\s*[A-Za-z]',text)):
                inside=False
        if inside:blocked.add(block['id'])
    for entry in structure.get('references',[]):
        blocked.update(part['block_id'] for part in entry.get('source_spans',[]))
    return blocked
