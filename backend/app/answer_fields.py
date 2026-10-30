"""Explicit question fields and scoped output checks, not semantic entailment."""
from __future__ import annotations
import re

# No paper identities or expected experimental values occur in this registry.
FIELDS={
 'gpu_clock':('GPU核心时钟频率',r'核心时钟|MHz|megahertz|core clock|clock frequency',
              'GPU core clock frequency MHz megahertz',[r'MHz|megahertz|core clock|clock frequency']),
 'gpu_voltage':('GPU运行电压',r'电压|voltage|\bvolts?\b','GPU operating voltage volts',[r'voltage|\bvolts?\b']),
 'gpu_count':('GPU/加速器数量',r'多少[张块].{0,16}(?:GPU|TPU|显卡)|(?:GPU|TPU|硬件|显卡).{0,8}(?:数量|多少[张块个])|(?:how many|number of).{0,25}(?:gpu|tpu)',
              'training hardware number GPUs TPUs chips', [r'\b\d[\d,]*\s+(?:[\w-]+\s+){0,4}(?:GPUs?|TPUs?|chips)\b']),
 'gpu_model':('GPU/加速器型号',r'型号|GPU类型|gpu (?:type|model)|what.{0,15}GPUs?',
              'training hardware NVIDIA GPU TPU type model', [r'\b(?:[A-Z]\d{2,3}|TPUs?|GPUs?)\b',r'NVIDIA|Tesla|Cloud TPU']),
 'gpu_memory':('单卡显存',r'显存|VRAM|gpu memory','training GPU memory VRAM GB', [r'\b\d+(?:\.\d+)?\s*(?:GB|GiB|MB)\b']),
 'duration':('训练耗时',r'耗时|多久|多少[天小时周]|how long|training time|duration|days to train',
             'training took elapsed days hours time duration', [r'\b\d[\d,.]*\s*(?:hours?|days?|seconds?|weeks?)\b']),
 'corpus':('训练语料',r'语料|training data|pre.?training data|data sources',
           'pre-training corpus training data dataset', [r'corpus|training data|data set|dataset',r'Wikipedia|collected|concatenat']),
 'dataset_size':('数据集规模',r'(?:数据集|图文对).{0,20}(?:多少|规模|大小)|dataset size|how many.{0,25}(?:pairs|examples)',
                 'dataset size number million image text pairs examples', [r'\b(?:million|billion)\b',r'\bpairs?\b']),
 'queries':('数据收集查询数',r'多少.{0,8}(?:查询|queries)|number of queries',
            'data collection number search queries', [r'\bqueries\b',r'\b\d[\d,]*\s+queries\b']),
 'objective':('训练目标/损失组成',r'损失|训练目标|loss|objective',
              'training objective loss distillation masked modeling contrastive symmetric cross entropy cosine',
              [r'loss|objective',r'cross.?entropy',r'distill|contrastive',r'cosine|masked.{0,25}model']),
 'components':('架构部件变化',r'移除|去掉|哪些部件|removed|remove.{0,15}(?:component|embedding)',
               'student architecture removed embeddings pooler components', [r'remov|architecture',r'embedding|pooler|component']),
 'layers':('层数及变化',r'层数|多少层|number of layers|how many layers',
           'model architecture number layers reduced depth', [r'\blayers?\b',r'reduced|factor|depth']),
 'architecture':('模型结构',r'架构|模型结构|architecture',
                 'model architecture encoder decoder structure', [r'architecture|encoder|decoder']),
 'vocabulary_size':('词表大小',r'词表.{0,12}(?:大小|多少|多大|规模)|vocabulary size|how many.{0,15}(?:wordpieces|subword)',
                    'vocabulary size wordpieces subword units', [r'vocabulary|wordpiece|subword',r'\b\d[\d,]*(?:K)?\b']),
 'tokenization':('分词工具/粒度',r'分词|tokeni[sz]|(?:词表|文本).{0,12}(?:工具|编码)|BPE',
                 'tokenization vocabulary BPE SentencePiece byte-level wordpiece', [r'SentencePiece|BPE|tokeniz|wordpiece']),
 'vocabulary_shared':('输入输出词表共享',r'(?:输入|input).{0,25}(?:输出|output).{0,15}(?:共享|shar)|(?:共享|shared).{0,15}(?:词表|vocab)',
                      'vocabulary shared input output embeddings', [r'\bshared\b',r'input.{0,30}output']),
 'batch_size':('批量大小',r'batch|批量|批大小','training batch size sequences minibatch', [r'batch|mini.?batch',r'sequences']),
 'sequence_length':('最大序列/上下文长度',r'序列长度|sequence length|上下文长度|context (?:window|length)',
                     'maximum sequence length context window tokens', [r'sequence length|context|maximum',r'tokens']),
 'training_steps':('训练步数',r'训练.{0,12}(?:步|steps)|步数|training steps',
                    'pre-training number steps iterations', [r'\bsteps?\b',r'iterations']),
 'epochs':('训练轮数',r'训练轮数|epochs|多少轮','training number epochs', [r'\bepochs?\b']),
 'optimizer':('优化器/超参数',r'优化器|optimizer|Adam|beta|β|权重衰减|学习率|learning rate|调度',
              'training optimizer Adam beta learning rate weight decay schedule warmup', [r'Adam|optimizer',r'beta|β|learning rate|weight decay|schedule']),
 'formula':('所问公式',r'公式|equation|formula','equation formula objective definition', [r'=|equation|formula']),
}


def intent_text(question):
    clauses=re.split(r'[，,；;。!?！？\n]',question)
    return ' '.join(clause for clause in clauses if not re.match(
        r'^\s*(?:不要(?!只)|不(?:从|用|根据|据此|做|要求|需要)|不得|禁止|do not|don\x27t|without)',clause,re.I))


def requested(question,limit=8):
    question=intent_text(question)
    found=[key for key,(_,pattern,_,_) in FIELDS.items() if re.search(pattern,question,re.I)]
    if 'components' in found and 'architecture' in found:found.remove('architecture')
    # "训练硬件" is a broad field only when no explicit hardware subfield exists.
    if not set(found)&{'gpu_count','gpu_model','gpu_memory','gpu_clock','gpu_voltage'} and re.search(r'训练硬件|training hardware|hardware used',question,re.I):
        found.insert(0,'gpu_model')
    return found[:limit] if limit else found


def cue_score(key,text):
    return sum(bool(re.search(pattern,text,re.I|re.S)) for pattern in FIELDS[key][3])


def probe_query(question,key):
    question=intent_text(question)
    sections=re.findall(r'(?:第\s*|\bsection\s+)(\d+(?:\.\d+)*)(?:\s*节|\b)',question,re.I)
    variants=re.findall(r'\b[A-Za-z][A-Za-z0-9/-]*\d[A-Za-z0-9/.-]*\b',question)
    phase='pre-training' if re.search(r'预训练|pre.?train',question,re.I) else 'fine-tuning' if re.search(r'微调|fine.?tun',question,re.I) else ''
    explicit=list(dict.fromkeys(re.findall(r'(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_-]{0,63}(?![A-Za-z0-9_-])',question)))[:12]
    return ' '.join([*['Section '+number for number in sections],phase,*variants[:3],FIELDS[key][2],*explicit]).strip()


def formula_fragment(text):
    """Reject obvious broken layout fragments, not prove a formula is correct."""
    if re.search(r'=\s*\^\s*\{|=\s*$|\^\s*\{\s*\}',text):return True
    return text.count('{')!=text.count('}')


def interpret(payload,question,evidence,validate,*,validate_audit=None):
    """Compile only allowlisted document/field rows; legacy callers still work."""
    keys=requested(question)
    if not keys or 'field_answers' not in payload:return None
    incoming=payload['field_answers']
    if not isinstance(incoming,list):raise ValueError('invalid_field_answers')
    by_id={item['id']:item for item in evidence};documents={item['document_id'] for item in evidence}
    groups={};rejected=0;decisions=[]
    for index,item in enumerate(incoming[:20]):
        decision={'index':index,'status':'rejected','reason':'invalid_document_or_field'};decisions.append(decision)
        if (not isinstance(item,dict) or not isinstance(item.get('document_id'),str) or not isinstance(item.get('field'),str)
                or item['document_id'] not in documents or item['field'] not in keys):
            rejected+=1;continue
        decision.update(document_id=item['document_id'],field=item['field'])
        key=(item['document_id'],item['field']);groups.setdefault(key,[]).append((item,decision))
    claims=[];coverage=[];missing=[]
    for document in dict.fromkeys(item['document_id'] for item in evidence):
        first=next(item for item in evidence if item['document_id']==document)
        title=first['title']+(' · '+first['version_label'] if first.get('version_label') else '')
        for field in keys:
            rows=groups.get((document,field),[]);accepted=[];states=set();invalid=False
            for item,decision in rows:
                states.add(item.get('status') if isinstance(item.get('status'),str) else 'invalid')
                if item.get('status')=='missing':
                    decision.update(status='missing_candidate',reason='provider_missing');continue
                ids=item.get('evidence_ids');text=item.get('text')
                if (item.get('status')!='answered' or not isinstance(ids,list) or not ids
                        or any(not isinstance(identity,str) or identity not in by_id or by_id[identity]['document_id']!=document for identity in ids)):
                    decision['reason']='invalid_status_or_document_evidence';invalid=True;rejected+=1;continue
                if validate_audit:
                    result,details=validate_audit({'claims':[{'text':text,'evidence_ids':ids}]},evidence)
                    check=details['decisions'][0]
                    decision.update({k:v for k,v in check.items() if k!='index'})
                else:
                    result=validate({'claims':[{'text':text,'evidence_ids':ids}]},evidence)
                    decision.update(status='accepted' if result else 'rejected',reason='literal_checks_passed' if result else 'source_or_structure_check')
                if not result:invalid=True;rejected+=1;continue
                claim={**result[0],'document_id':document,'field':field}
                if claim not in accepted:accepted.append(claim)
            claims.extend(accepted)
            status='needs_review' if invalid or len(states)>1 or len(accepted)>1 else 'has_candidate_answer' if accepted else 'missing_in_evidence' if states=={'missing'} else 'not_assessed'
            coverage.append({'document_id':document,'title':title,'field':field,'label':FIELDS[field][0],
                             'status':status,'evidence_ids':list(dict.fromkeys(identity for claim in accepted for identity in claim['evidence_ids']))})
            if status in {'missing_in_evidence','not_assessed','needs_review'}:
                detail={'missing_in_evidence':'给定候选证据不足','not_assessed':'本次模型未完成该字段','needs_review':'输出冲突或未通过来源/结构检查'}[status]
                missing.append(title+' · '+FIELDS[field][0]+'：'+detail+'。')
    return {'claims':claims,'missing_information':missing,'answer_coverage':coverage,
            'field_validation':{'mode':'scoped_fields','version':'qa-numeric-literals-v3','rejected_rows':rejected,'semantic_verified':False,
                                'proposal_count':len(incoming),'truncated':max(0,len(incoming)-20),'decisions':decisions}}
