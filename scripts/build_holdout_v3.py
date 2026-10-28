"""Author a fresh-work draft pack after product freeze, before any /ask calls."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app import store
from scripts.eval_core import text_hash,validate_pack,work_alias

D='distilbert';C='clip'
CASES=[
 ('N01',[D],'DistilBERT 的学生架构相对 BERT 移除了哪些部件，层数如何变化？','去掉token-type embeddings和pooler，层数减半。',[(D,'blk_bdaaa4e683c644e8b409eef9f29df633')]),
 ('N02',[D],'DistilBERT 的最终训练目标包含哪些损失，它们分别起什么作用？','蒸馏损失、MLM监督损失、对齐学生与教师隐藏状态方向的余弦嵌入损失。',[(D,'blk_a039fb65581440cd8746d416f549761b')]),
 ('N03',[D],'DistilBERT 使用什么预训练语料，训练硬件的数量、型号、单卡显存和训练耗时分别是多少？不要混入文中对照模型的设置。','英语Wikipedia和Toronto Book Corpus；8张16GB V100；约90小时。',[(D,'blk_2ffd7aa9c7c84ce0b58ca596cd773708')]),
 ('N04',[C],'CLIP 第2.2节构建的数据集包含多少图文对，收集过程使用了多少条文本查询？','4亿图文对；50万条查询。',[(C,'blk_e7ef20f27c3847dbbdd97bc46538f9db')]),
 ('N05',[C],'CLIP 第2.5节报告的训练轮数、minibatch大小、优化器和学习率调度分别是什么？','32轮；32,768；Adam并解耦权重衰减；余弦学习率调度。',[(C,'blk_ec610674a2e84411912bcc6e3e458864')]),
 ('N06',[C],'CLIP 中 RN50x64 与最大的 Vision Transformer 分别使用多少张什么GPU，训练了多少天？','RN50x64：592张V100、18天；最大ViT：256张V100、12天。',[(C,'blk_ec610674a2e84411912bcc6e3e458864')]),
 ('N07',[D,C],'分别比较 DistilBERT 和 CLIP 的 RN50x64 训练GPU数量、型号与耗时，保留具体模型条件，不据此给效率排名。','DistilBERT：8张16GB V100、约90小时；CLIP RN50x64：592张V100、18天。',[(D,'blk_2ffd7aa9c7c84ce0b58ca596cd773708'),(C,'blk_ec610674a2e84411912bcc6e3e458864')]),
 ('N08',[D,C],'比较 DistilBERT 和 CLIP 各自训练目标的损失组成，不把两篇的目标混在一起。','DistilBERT：蒸馏、MLM、余弦嵌入；CLIP：图文相似度上的对称交叉熵，拉近真配对、远离错误配对。',[(D,'blk_a039fb65581440cd8746d416f549761b'),(C,'blk_ab309ebc647e443fbd4d5451091e1112')]),
]


def main():
    sources={};blocks={};documents={}
    for key,filename,work,title in [
        (D,'1910.01108v4.pdf','1910.01108','DistilBERT, a distilled version of BERT: smaller, faster, cheaper and lighter'),
        (C,'2103.00020v1.pdf','2103.00020','Learning Transferable Visual Models From Natural Language Supervision')]:
        document=next(d for d in store.list_documents() if d['filename']==filename)
        structure=store.get_structure(document['id']);documents[key]=document
        sources[key]={k:document.get(k) for k in ('sha256','version_label','page_count','status','warnings')}
        sources[key].update(title=title,work=work,aliases=['arxiv:'+work],split='holdout',filename=filename,
                            original_parse_run_id=structure['parse_run_id'])
        blocks[key]={b['id']:b for b in structure['blocks']}
    prior=json.loads(Path('evaluation/qa_v1.json').read_text(encoding='utf-8'))
    assert not {work_alias(s['work']) for s in sources.values()} & {work_alias(s['work']) for s in prior['sources'].values()}
    cases=[]
    for identity,scope,question,expected,anchors in CASES:
        groups=[]
        for key,block_id in anchors:
            block=blocks[key][block_id]
            groups.append([{'source':key,'page':block['page'],'bbox':block['bbox'],
                            'text_sha256':text_hash(block['text']),'original_block_id':block_id}])
        cases.append({'id':identity,'split':'holdout','kind':'cross_document' if len(scope)>1 else 'single_document',
                      'answerable':True,'sources':scope,'question':question,'expected_facts':expected,
                      'gold_groups':groups,'gold_review':'assistant_authored_pending_independent_human_review'})
    negatives=[
       {'id':'G01','sources':[D],'question':'DistilBERT 训练所用 V100 GPU 的核心时钟频率是多少 MHz？只回答论文明确报告的值，不从型号推断。',
        'missing_field':'GPU核心时钟频率MHz','absence_patterns':['megahertz','\\bmhz\\b','core clock','clock frequency']},
       {'id':'G02','sources':[C],'question':'CLIP 训练所用 V100 GPU 的运行电压是多少伏特？只回答论文明确报告的值，不从型号推断。',
        'missing_field':'GPU运行电压V','absence_patterns':['voltage','\\bvolts?\\b']},
    ]
    import re
    for case in negatives:
        full='\n'.join(b['text'] for b in blocks[case['sources'][0]].values())
        counts={pattern:len(re.findall(pattern,full,re.I)) for pattern in case['absence_patterns']}
        assert not any(counts.values())
        case.update(absence_text_sha256=text_hash(full),absence_term_counts=counts,
                    gold_review='Assistant text search plus hardware-section inspection; not independent proof/adjudication')
    pack={'schema_version':1,'name':'Fresh-work draft v3','sources':sources,'cases':cases,'negative_checks':negatives,
          'model_sample':['N01','N03','N04','N06','N07','N08'],
          'scope':'8 fresh-work answerable draft questions: 6 single-document, 2 cross-document; 2 separately reported missing-field probes',
          'holdout_limit':'Product b7dc401 fixed before question authoring; no /ask calls on these papers before pack freeze. Same assistant authored gold after inspecting papers. Public foundational papers may occur in provider pretraining; no independent human quality score.'}
    validate_pack(pack)
    with Path('evaluation/qa_holdout_v3.json').open('x',encoding='utf-8') as stream:json.dump(pack,stream,ensure_ascii=False,indent=2)
    print('Authored 8 factual cases, 2 separate missing-field probes; fixed 6-case live sample; no /ask calls.')


if __name__=='__main__':main()
