"""One-time authoring of a version-bound draft gold pack; refuses overwrite."""
from __future__ import annotations
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import store
from scripts.eval_core import text_hash, validate_pack

SOURCES = {
 'transformer': ('1706.03762v7.pdf', '1706.03762', 'development', ['arxiv:1706.03762v7']),
 'bert': ('N19-1423.pdf', '1810.04805', 'development', ['arxiv:1810.04805', 'acl:N19-1423']),
 'roberta': ('1907.11692v1.pdf', '1907.11692', 'holdout', ['arxiv:1907.11692v1']),
 't5': ('1910.10683v4.pdf', '1910.10683', 'holdout', ['arxiv:1910.10683v4']),
}
# Block UUIDs identify the authoring snapshot only; portable anchors use exact text hash/page/box.
T='transformer'; B='bert'; R='roberta'; F='t5'
CASES = [
 ('D01',[T],'Transformer 训练使用了多少张、什么型号的 GPU？','8 张 NVIDIA P100。',[(T,'blk_f65dea2c2d984ee5936cb2a4fc234b60')]),
 ('D02',[T],'Transformer 的 base 与 big 模型分别训练多少步、耗时多久？','base：100,000 步、12 小时；big：300,000 步、3.5 天。',[(T,'blk_f65dea2c2d984ee5936cb2a4fc234b60')]),
 ('D03',[T],'Transformer 报告的 Adam beta1 和 beta2 分别是多少？','beta1=0.9，beta2=0.98。',[(T,'blk_ebaf2a0688bd4306a8213a4e91685eea')]),
 ('D04',[T],'Transformer 编码器包含多少层？各子层输出维度 d_model 是多少？','6 个相同层；d_model=512。',[(T,'blk_92f24f2fb7e14004a35e91653a73be73')]),
 ('D05',[B],'BERTBASE 与 BERTLARGE 的参数量分别是多少？','BERTBASE 110M；BERTLARGE 340M。',[(B,'blk_f5b0068e60ab48968d5b3de51db1e447')]),
 ('D06',[B],'BERT 预训练的批量大小、每序列最大 token 长度和训练步数是多少？','256 序列，512 token，1,000,000 步；不代表全程都用512长度。',[(B,'blk_873442ffd74c4fc2bb84af6f409337e9')]),
 ('D07',[B],'BERT 预训练 Adam 的学习率、beta1、beta2 与 L2 权重衰减是多少？','1e-4、0.9、0.999、0.01。',[(B,'blk_a69f82bb04c140d0b16a07f776165da6')]),
 ('D08',[B],'BERTBASE 与 BERTLARGE 各用多少 Cloud TPU、多少芯片，预训练多久？','BASE：4 TPU/16芯片；LARGE：16 TPU/64芯片；各4天。',[(B,'blk_c92146c6fb834b4cafe0f955b297a51d')]),
 ('H01',[R],'RoBERTa 最终配置采用什么 masking 策略，是否使用 NSP loss？','dynamic masking；FULL-SENTENCES，去掉 NSP loss。',[(R,'blk_8720154031034a7b9873d1d9e2514e24')]),
 ('H02',[R],'RoBERTa 使用什么粒度的 BPE，词表包含多少个 subword units？','byte-level BPE，50K subword units。',[(R,'blk_202c450f07194636a18afb06afab2131')]),
 ('H03',[R],'RoBERTa 第5节加入所有额外数据后，总预训练文本规模是多少？','160GB。',[(R,'blk_d1e52fd96fba4cd886398dec25aea1db')]),
 ('H04',[R],'RoBERTa 第5节依次把预训练步数从多少增加到多少，最长训练多少步？','100K 到 300K，再到 500K；最长500K。',[(R,'blk_5f9938f1ad3a46818b90e2695371af03')]),
 ('H05',[R],'RoBERTa 第4.3节之后的实验采用多少条序列的 batch？','8K sequences；区别于文中复述的BERT 256。',[(R,'blk_57e075ddeeef4b6fbbd98cebfced4834')]),
 ('H06',[F],'T5 的 C4 基础数据集来自哪一月份的网页抽取文本，清洗后约多大？','2019年4月；约750GB。',[(F,'blk_a73851cb065c42cea8f4cc0293c2e336')]),
 ('H07',[F],'T5 第3.1节基线预训练多少步，最大序列长度和 batch 序列数分别是多少？','524,288步；512；128序列。限基线设置。',[(F,'blk_0b0e9169887b4f00adfdb15db57c3a15')]),
 ('H08',[F],'T5 的词表用什么工具编码，词表大小是多少，输入输出是否共享？','SentencePiece；32,000 wordpieces；输入输出共享。',[(F,'blk_a8a26a5435a44f8f8757808c37234798'),(F,'blk_dc2ac8b251a747a7a3ec33f4953ca715')]),
 ('H09',[F],'T5 第3.1节基线微调步数与恒定学习率是多少？','262,144步；0.001；限基线微调。',[(F,'blk_c1389f309d4647b69c410687c40b828b')]),
 ('H10',[F],'T5 第3.1.4节基线目标丢弃输入 token 的比例是多少，连续缺失片段用什么替代？','15%；每个连续片段由一个独特 sentinel token 替代。',[(F,'blk_2699432496f54d06ac91b9a280df1a93')]),
 ('H11',[R,F],'分别比较 RoBERTa 与 T5 使用的词表大小及分词工具或粒度，不混淆原始BERT的词表。','RoBERTa：byte-level BPE 50K；T5：SentencePiece 32,000 wordpieces。',[(R,'blk_202c450f07194636a18afb06afab2131'),(F,'blk_a8a26a5435a44f8f8757808c37234798')]),
 ('H12',[R,F],'分别给出 RoBERTa 第5节合并数据与 T5 的 C4 清洗数据的文本体积，不把大小当成质量比较。','RoBERTa 合并160GB；T5 C4约750GB；语料与清洗条件不同。',[(R,'blk_d1e52fd96fba4cd886398dec25aea1db'),(F,'blk_a73851cb065c42cea8f4cc0293c2e336')]),
]


def main():
    target = Path('evaluation/qa_v1.json')
    if target.exists():
        raise SystemExit('Refusing overwrite; create a new pack version instead.')
    sources = {}; blocks = {}
    for key, (filename, work, split, aliases) in SOURCES.items():
        document = next(d for d in store.list_documents() if d['filename'] == filename)
        structure = store.get_structure(document['id'])
        sources[key] = {k: document.get(k) for k in ('sha256','version_label','title','page_count','arxiv_id')}
        sources[key].update(work=work, aliases=aliases, split=split, filename=filename,
                            original_parse_run_id=structure['parse_run_id'], parser=structure.get('parse_run'))
        blocks[key] = {b['id']: b for b in structure['blocks']}
    cases=[]
    for identity, scope, question, expected, anchors in CASES:
        groups=[]
        for source, block_id in anchors:
            block=blocks[source][block_id]
            groups.append([{'source':source,'page':block['page'],'bbox':block['bbox'],
                            'text_sha256':text_hash(block['text']),'original_block_id':block_id}])
        cases.append({'id':identity,'split':sources[scope[0]]['split'],'kind':'cross_document' if len(scope)>1 else 'single_document',
                      'answerable':True,'sources':scope,'question':question,'expected_facts':expected,
                      'gold_groups':groups,'gold_review':'assistant_authored_draft_pending_independent_human_review'})
    pack={'schema_version':1,'name':'ScholarTrace QA draft v1','sources':sources,'cases':cases,
          'scope':'20 answerable factual questions; 8 development / 12 work-disjoint holdout. No unanswerable accuracy is claimed.',
          'holdout_limit':'Paper-disjoint from earlier product tuning; authored by the same assistant. Public foundational papers may be in provider pretraining. Not a blind or independently adjudicated benchmark.'}
    validate_pack(pack);target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(pack,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Created draft pack:',len(cases),'cases')


if __name__=='__main__':main()
