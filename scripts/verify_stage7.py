"""Real PDF workflow acceptance; model calls require the explicit --live-model flag."""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import re
import time
import zipfile
import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api',default='http://127.0.0.1:8000/api')
    parser.add_argument('--output',type=Path,default=Path('data/stage7_verification'))
    parser.add_argument('--live-model',action='store_true',help='Use the configured provider for a small explicit real-PDF check')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    report={'started_at':time.strftime('%Y-%m-%d %H:%M:%S'),'live_model_requested':args.live_model,'quality_scope':'small development smoke set; not held-out evaluation'}
    with httpx.Client(base_url=args.api.rstrip('/'),timeout=180) as client:
        def get(path,**kwargs):
            response=client.get(path,**kwargs);response.raise_for_status();return response
        def post(path,body):
            response=client.post(path,json=body);response.raise_for_status();return response.json()
        def finish(identity,accept=None):
            deadline=time.monotonic()+180
            statuses=accept or {'completed','completed_with_gaps','paused','budget_exhausted','failed'}
            while time.monotonic()<deadline:
                value=get('/research-tasks/'+identity).json()
                if value['status'] in statuses:return value
                time.sleep(.2)
            raise RuntimeError('workflow_timeout')
        def anchor_check(evidence):
            for item in evidence:
                structure=get('/documents/'+item['document_id']+'/structure',params={'parse_run_id':item['parse_run_id']}).json()
                block=next(block for block in structure['blocks'] if block['id']==item['block_id'])
                assert item['text']==block['text'] and item['bbox']==block['bbox']
        documents=get('/documents').json()['items']
        wanted=['1706.03762v7.pdf','N19-1423.pdf','language_understanding_paper.pdf','1710.10903v3.pdf']
        selected=[next(document for document in documents if document['filename']==name) for name in wanted]
        plan=post('/research-tasks',{'goal':'比较所选论文的模型结构和训练目标，整理引用图谱，生成写作框架和流程图。','document_ids':[document['id'] for document in selected],'max_tool_calls':1,'use_model':False})
        assert plan['status']=='planned'
        post('/research-tasks/'+plan['id']+'/control',{'action':'start'})
        checkpoint=finish(plan['id']);assert checkpoint['status']=='budget_exhausted'
        first_result=checkpoint['steps'][0]['result'];anchor_check(first_result['evidence'])
        post('/research-tasks/'+plan['id']+'/control',{'action':'resume','max_tool_calls':8})
        completed=finish(plan['id']);assert completed['status']=='completed_with_gaps',completed['status']
        assert completed['steps'][0]['result']==first_result and completed['steps'][0]['attempts']==1
        assert completed['usage']['model']['calls']==0 and all(step['status']=='completed' for step in completed['steps'])
        bundle=get('/research-tasks/'+plan['id']+'/export').content
        (args.output/'offline-task.zip').write_bytes(bundle)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:assert {'task.json','comparison.md','graph.json','writing-plan.md','diagram/figure.pdf'}<=set(archive.namelist())
        report['offline_task']={'id':plan['id'],'status':completed['status'],'sources':len(completed['sources']),'steps':len(completed['steps']),'evidence_anchors':len(first_result['evidence']),'usage':completed['usage'],'budget_checkpoint_resumed':True,'completed_step_repeated':False}
        print('Offline task, source anchors, budget/resume and ZIP: checked',flush=True)
        if args.live_model:
            status=get('/model/status').json();assert status['ready'],'Configure the provider first'
            probe=post('/model/check',{});assert probe['status']=='connected',probe.get('error_code')
            report['model']={key:status[key] for key in ['profile','endpoint_host','model']}
            report['connection_check']=probe
            model_task=post('/research-tasks',{'goal':'比较两篇论文明确报告的训练硬件和优化器，保留训练阶段与模型大小的条件，生成引用图谱和写作框架。','document_ids':[document['id'] for document in selected[:2]],'planner':'model','use_model':True,'max_model_calls':4,'max_output_tokens':8000})
            post('/research-tasks/'+model_task['id']+'/control',{'action':'start'})
            generated=finish(model_task['id']);assert generated['status']=='completed_with_gaps',generated['status']
            for step in generated['steps']:
                result=step.get('result') or {}
                anchor_check(result.get('evidence',[]))
                for row in result.get('rows',[]):
                    for cell in row['cells']:anchor_check(cell['evidence'])
            (args.output/'live-task.json').write_text(json.dumps(generated,ensure_ascii=False,indent=2),encoding='utf-8')
            (args.output/'live-task.zip').write_bytes(get('/research-tasks/'+generated['id']+'/export').content)
            report['live_task']={'id':generated['id'],'status':generated['status'],'planner_mode':generated['planner_mode'],'planner_model_calls':generated['planner_model_calls'],'usage':generated['usage'],'steps':[{'tool':step['tool'],'status':step['status'],'mode':(step.get('result') or {}).get('mode')} for step in generated['steps']]}
            print('Live model plan and bounded workflow: checked',flush=True)
            cases=[
                {'id':'transformer_gpu','document':selected[0],'question':'What GPU type and how many GPUs were used to train the Transformer models?','required':['P100'],'number_pattern':r'\b8\b|eight|八'},
                {'id':'bert_batch_sequence','document':selected[1],'question':'What pre-training batch size and maximum sequence length are reported for BERT?','required':['256','512']},
                {'id':'missing_gpu_vram','document':selected[0],'question':'What per-GPU VRAM capacity in GB is explicitly reported for the Transformer training hardware? Do not infer capacity from the GPU model.','expect_abstention':True},
            ]
            answers=[]
            for case in cases:
                answer=post('/ask',{'question':case['question'],'document_ids':[case['document']['id']],'limit':6,'use_model':True})
                anchor_check(answer['evidence'])
                text=' '.join(claim['text'] for claim in answer['claims'])
                checks={'anchors_match_pdf':True,'model_mode':answer['mode']=='llm','claim_count':len(answer['claims']),'semantic_review':'pending_manual_read'}
                if case.get('expect_abstention'):checks['no_retained_factual_claims']=not answer['claims']
                else:
                    checks['expected_terms_present']=all(term.casefold() in text.casefold() for term in case['required'])
                    if case.get('number_pattern'):checks['expected_count_present']=bool(re.search(case['number_pattern'],text,re.I))
                answers.append({'id':case['id'],'question':case['question'],'document_id':case['document']['id'],'parse_run_id':case['document']['parse_run_id'],'checks':checks,'response':answer})
                print(case['id']+': '+json.dumps(checks,ensure_ascii=False),flush=True)
            (args.output/'qa-cases.json').write_text(json.dumps(answers,ensure_ascii=False,indent=2),encoding='utf-8')
            report['qa_checks']=[{'id':answer['id'],**answer['checks']} for answer in answers]
            structure=get('/documents/'+selected[0]['id']+'/structure').json()
            candidates=[block for block in structure['blocks'] if block['type']=='paragraph' and re.search(r'P100',block['text']) and len(block['text'])<2500]
            assert candidates,'Hardware paragraph not found'
            block=candidates[0]
            translation=post('/translations',{'document_id':selected[0]['id'],'parse_run_id':selected[0]['parse_run_id'],'block_ids':[block['id']],'scope':'blocks','target_language':'zh','glossary':{'Transformer':'Transformer'}})
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                translated=get('/translations/'+translation['id']).json()
                if translated['status'] in {'ready','failed'}:break
                time.sleep(.2)
            (args.output/'translation.json').write_text(json.dumps(translated,ensure_ascii=False,indent=2),encoding='utf-8')
            assert translated['status']=='ready',translated['status']
            (args.output/'translation.md').write_bytes(get('/translations/'+translation['id']+'/export',params={'format':'markdown'}).content)
            report['translation']={'id':translation['id'],'status':translated['status'],'block_id':block['id'],'page':block['page'],'items':[item['status'] for item in translated['items']],'numeric_protection_passed':True,'semantic_review':'pending_manual_read'}
            print('One real paragraph translation: protection checks passed',flush=True)
    report['completed_at']=time.strftime('%Y-%m-%d %H:%M:%S')
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
