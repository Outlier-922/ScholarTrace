"""Build a local-only HTML review sheet from saved responses; no API calls."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.eval_core import digest, validate_pack


def collect(pack, rows, run_id):
    validate_pack(pack)
    cases = {case['id']: case for case in pack['cases']}
    if not rows or len({row['case_id'] for row in rows}) != len(rows):
        raise ValueError('empty_or_duplicate_run_cases')
    items = []
    for row in rows:
        case = cases.get(row['case_id'])
        if not case or digest(case) != row['case_sha256']:
            raise ValueError('case_changed_since_run')
        response = row.get('response')
        if response is not None and digest(response) != row.get('response_sha256'):
            raise ValueError('response_changed_since_run')
        response = response or {}
        items.append({
            'case_id': case['id'], 'question': case['question'],
            'expected_facts': case.get('expected_facts', ''),
            'gold_groups': [[{**anchor, 'title': pack['sources'][anchor['source']]['title'],
                              'version_label': pack['sources'][anchor['source']].get('version_label')}
                             for anchor in group] for group in case['gold_groups']],
            'case_sha256': row['case_sha256'], 'response_sha256': row.get('response_sha256'),
            'mode': response.get('mode', row.get('error_type', 'error')),
            'live_model_requested': bool(row.get('live_model_requested')),
            'answer': response.get('answer', ''), 'claims': response.get('claims', []),
            'missing_information': response.get('missing_information', []),
            'coverage': response.get('answer_coverage', []),
            'evidence': [{key: item.get(key) for key in
                          ('id', 'title', 'version_label', 'page', 'bbox', 'document_id',
                           'parse_run_id', 'block_id', 'text', 'metadata')}
                         for item in response.get('evidence', [])],
        })
    return {'run_id': run_id, 'items': items}


STYLE = """
:root{font-family:'Microsoft YaHei',system-ui,sans-serif;color:#243c35;background:#f3f6f2;line-height:1.65}
*{box-sizing:border-box}body{margin:0;overflow-wrap:anywhere}header,main{max-width:1120px;margin:auto;padding:22px}header{padding-bottom:8px}
h1{margin:0;font-size:28px}h2{font-size:19px}h3{font-size:15px}p{margin:8px 0}.notice{color:#805d23}
nav{display:flex;flex-wrap:wrap;gap:10px;align-items:center}button,.import{font:inherit;background:#315e4c;color:white;border:0;border-radius:5px;padding:8px 13px;cursor:pointer}
input,select,textarea{font:inherit;padding:7px;border:1px solid #bccbc0;border-radius:4px;background:white;max-width:100%}
input[type=number]{width:90px}input[type=file]{display:none}article{background:white;border:1px solid #d5ded4;border-radius:9px;padding:22px;margin:18px 0}
.columns{display:grid;grid-template-columns:1fr 1fr;gap:22px}.text{white-space:pre-wrap;overflow-wrap:anywhere}.muted,small{color:#617367}
.evidence{background:#f6f8f4;padding:12px;margin:9px 0;border-left:3px solid #708e65;scroll-margin-top:15px}
label{display:block;margin:12px 0 4px}textarea{display:block;width:100%;min-height:95px}.grade{border-top:1px solid #d9e1d5;margin-top:18px;padding-top:10px}
.grade-row{display:flex;gap:18px;flex-wrap:wrap}.status{font-weight:600;color:#365e4c}a{color:#315e4c}summary{cursor:pointer;font-weight:600}
code{font-size:11px;overflow-wrap:anywhere}.invalid{color:#a23225}@media(max-width:720px){.columns{grid-template-columns:1fr}header,main{padding:14px}article{padding:14px}h1{font-size:23px}}
"""

SCRIPT = r"""
'use strict';
const data=JSON.parse(document.getElementById('review-data').textContent);
const states=data.items.map(item=>({case_id:item.case_id,case_sha256:item.case_sha256,response_sha256:item.response_sha256,status:'pending',reviewer:'',judgment:null,supported_claims:null,notes:'',standard_facts_status:'pending'}));
const controls=[];
function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=typeof text==='string'?text:JSON.stringify(text,null,2);if(cls)node.className=cls;return node;}
function select(values){const node=el('select');for(const [value,label] of values){const option=el('option',label);option.value=value;node.append(option);}return node;}
function inputLabel(container,label,node){const wrap=el('label',label);wrap.append(el('br'),node);container.append(wrap);return node;}
function factsText(value){return typeof value==='string'?value:JSON.stringify(value,null,2);}
function sync(){let count=0;states.forEach((state,i)=>{const c=controls[i];state.reviewer=c.reviewer.value.trim();state.judgment=c.judgment.value||null;state.supported_claims=c.support.value===''?null:Number(c.support.value);state.notes=c.notes.value.trim();state.standard_facts_status=c.gold.value;const ok=!!state.reviewer&&!!state.judgment&&!!state.notes&&state.standard_facts_status!=='pending'&&(state.supported_claims===null||Number.isInteger(state.supported_claims)&&state.supported_claims>=0&&state.supported_claims<=c.claims);state.status=c.confirm.checked&&ok?'reviewed':'pending';if(state.status==='reviewed')count++;c.message.textContent=c.confirm.checked&&!ok?'信息未齐：仍保存为待复核。':state.status==='reviewed'?'已由填写者标记复核完成':'待人工复核';c.message.className=c.confirm.checked&&!ok?'invalid':'status';});document.getElementById('progress').textContent=`${count}/${states.length} 已标记完成；此处不自动计算准确率。`;}
data.items.forEach((item,i)=>{
 const card=el('article');card.id='case-'+i;card.append(el('h2',item.case_id+' · '+item.question));card.append(el('p','模式：'+item.mode+'；保留陈述：'+item.claims.length+'；响应哈希：'+(item.response_sha256||'无响应'),'muted'));
 const columns=el('div',undefined,'columns'),answer=el('section'),gold=el('section');answer.append(el('h3','原始保留回答'),el('div',item.answer||'没有保留回答','text'));answer.append(el('h3','缺失说明'),el('div',factsText(item.missing_information),'text'));gold.append(el('h3','标准事实草稿 · 也需要核对'),el('div',factsText(item.expected_facts),'text'));gold.append(el('h3','指定证据组定位'));
 item.gold_groups.forEach((group,g)=>{const details=el('details');details.append(el('summary','证据组 '+(g+1)));group.forEach(a=>details.append(el('p',`${a.title} ${a.version_label||''} · PDF第${a.page}页`),el('code',JSON.stringify({bbox:a.bbox,text_sha256:a.text_sha256}))));gold.append(details);});columns.append(answer,gold);card.append(columns);
 if(item.coverage.length){const details=el('details');details.append(el('summary','字段状态（不是正确性裁决）'),el('pre',JSON.stringify(item.coverage,null,2),'text'));card.append(details);}
 card.append(el('h3','逐条陈述与引用'));
 item.claims.forEach((claim,j)=>{const line=el('div',undefined,'evidence');line.append(el('strong',String(j+1)+'. '),el('span',claim.text));for(const id of claim.evidence_ids||[]){const n=item.evidence.findIndex(e=>e.id===id);if(n<0){line.append(el('p','失效引用：'+id,'invalid'));continue;}const link=el('a',' [查看 '+id+']');link.href='#ev-'+i+'-'+n;line.append(link);}card.append(line);});
 const sources=el('details');sources.open=true;sources.append(el('summary','候选原文片段（请另在原PDF核对版式与条件）'));
 item.evidence.forEach((source,j)=>{const node=el('div',undefined,'evidence');node.id='ev-'+i+'-'+j;node.append(el('strong',`${source.title} ${source.version_label||''} · PDF第${source.page}页`),el('p',source.id),el('div',source.text||'','text'));const layout=source.metadata&&source.metadata.layout_text;if(layout&&layout!==source.text){const d=el('details');d.append(el('summary','保存的版式候选（上标等，仍需核对原页）'),el('div',layout,'text'));node.append(d);}node.append(el('code',JSON.stringify({document_id:source.document_id,parse_run_id:source.parse_run_id,block_id:source.block_id,bbox:source.bbox})));sources.append(node);});card.append(sources);
 const grade=el('section',undefined,'grade'),row=el('div',undefined,'grade-row');grade.append(el('h3','人工复核记录'));
 const reviewer=inputLabel(row,'真实复核人',el('input'));reviewer.type='text';reviewer.maxLength=120;
 const judgment=inputLabel(row,'完整回答判定',select([['','待判定'],['correct','完整正确'],['partial','部分正确'],['incorrect','错误'],['abstain','未保留事实 / 拒答'],['error','服务或运行错误']]));
 const canGrade=item.live_model_requested&&item.mode==='llm'&&item.claims.length>0;for(const option of judgment.options){if(['correct','partial','incorrect'].includes(option.value))option.disabled=!canGrade;if(option.value==='abstain')option.disabled=item.claims.length>0;}
 const support=inputLabel(row,'有语义支持的陈述条数（可留空）',el('input'));support.type='number';support.min='0';support.max=String(item.claims.length);support.step='1';
 const goldCheck=inputLabel(row,'标准事实草稿核对',select([['pending','尚未核对'],['confirmed','已核对，无需修订'],['needs_revision','需修订（在依据中说明）']]));grade.append(row);
 const notes=inputLabel(grade,'判定依据：缺失字段、条件、错误或标准事实修订说明',el('textarea'));const confirm=el('input');confirm.type='checkbox';inputLabel(grade,'我已核对原文及完整回答，确认本题记录',confirm);const message=el('p','待人工复核','status');grade.append(message);card.append(grade);document.getElementById('cases').append(card);
 const c={reviewer,judgment,support,gold:goldCheck,notes,confirm,message,claims:item.claims.length};controls.push(c);for(const node of [reviewer,judgment,support,goldCheck,notes,confirm])node.addEventListener('input',sync);
});
document.getElementById('download').addEventListener('click',()=>{sync();const url=URL.createObjectURL(new Blob([JSON.stringify(states,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='reviews-'+data.run_id+'.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
document.getElementById('import').addEventListener('change',async event=>{try{const file=event.target.files[0];if(!file)return;const rows=JSON.parse(await file.text());if(!Array.isArray(rows)||rows.length!==states.length)throw Error('题数不同');const map=new Map(rows.map(r=>[r.case_id,r]));if(map.size!==rows.length)throw Error('题号重复');states.forEach(s=>{const r=map.get(s.case_id);if(!r||r.case_sha256!==s.case_sha256||r.response_sha256!==s.response_sha256)throw Error('题目或响应哈希不同');if(!['pending','reviewed'].includes(r.status)||!['pending','confirmed','needs_revision'].includes(r.standard_facts_status)||typeof r.reviewer!=='string'||typeof r.notes!=='string'||r.judgment!==null&&!['correct','partial','incorrect','abstain','error'].includes(r.judgment)||r.supported_claims!==null&&!Number.isInteger(r.supported_claims))throw Error('记录格式无效');});states.forEach((s,i)=>{const r=map.get(s.case_id),c=controls[i];c.reviewer.value=r.reviewer;c.judgment.value=r.judgment||'';if(c.judgment.selectedOptions[0]?.disabled)c.judgment.value='';c.support.value=r.supported_claims===null?'':r.supported_claims;c.gold.value=r.standard_facts_status;c.notes.value=r.notes;c.confirm.checked=r.status==='reviewed';});sync();document.getElementById('import-status').textContent='已恢复本次记录。';}catch(e){document.getElementById('import-status').textContent='无法导入：'+e.message;}event.target.value='';});
document.getElementById('run').textContent=data.run_id;
window.addEventListener('beforeunload',event=>{if(states.some(s=>s.reviewer||s.notes||s.judgment)){event.preventDefault();event.returnValue='';}});
sync();
"""


def render(data):
    def sha(value):
        return base64.b64encode(hashlib.sha256(value.encode('utf-8')).digest()).decode('ascii')
    payload = json.dumps(data, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    csp = f"default-src 'none'; script-src 'sha256-{sha(SCRIPT)}'; style-src 'sha256-{sha(STYLE)}'; connect-src 'none'; base-uri 'none'; form-action 'none'"
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}"><title>研迹 · 离线人工复核</title><style>{STYLE}</style>
<header><h1>研迹 · 离线人工复核</h1><p>运行：<code id="run"></code></p>
<p class="notice">仅供本机复核，含论文片段。不会联网、调用模型或自动判正确。标准事实也是草稿；先在原PDF核对来源和条件。</p>
<p>完整正确要求覆盖全部所问字段、条件与单位，无实质额外错误和冲突缺失说明。部分正确或原始标准有误时请说明依据。这里的填写不能自动证明独立复核。</p>
<nav><button id="download">导出复核 JSON</button><label class="import">导入此前记录<input id="import" type="file" accept="application/json,.json"></label><span id="progress"></span></nav>
<p id="import-status" role="status"></p><p class="muted">页面不自动保存。填写后导出JSON，重开页面可导入恢复；最后用离线CLI核对原运行并生成报告。</p></header>
<main id="cases"></main><script id="review-data" type="application/json">{payload}</script><script>{SCRIPT}</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--pack', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    pack = json.loads(args.pack.read_text(encoding='utf-8'))
    rows = json.loads((args.run / 'raw.json').read_text(encoding='utf-8'))
    document = render(collect(pack, rows, args.run.name))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(document)
    print(f'Local-only review page created: {args.output}; {len(rows)} pending cases; zero API calls.')


if __name__ == '__main__':
    main()
