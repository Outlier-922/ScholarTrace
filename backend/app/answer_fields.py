"""Explicit question fields and scoped output checks, not semantic entailment."""
from __future__ import annotations
import re

SCOPED_FIELDS = {'parameter_updates', 'reference_policy', 'component_presence'}

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
              'training objective loss distillation contrastive symmetric cross entropy cosine masked language modeling next sentence prediction log likelihood label smoothing',
              [r'loss|objective',r'cross.?entropy|log likelihood|label smoothing',r'distill|contrastive',r'cosine|masked.{0,25}model']),
 'parameter_updates':('训练与冻结的参数或模型',
                      r'冻结|哪些参数.{0,6}(?:训练|更新)|优化哪(?:个|一种|一个)?模型|参数更新范围|\b(?:frozen|freeze|trainable weights|trainable parameters)\b|which (?:model|policy|weights) (?:is|are) (?:optimized|updated)',
                      'fine tuning trainable parameters frozen pretrained model weights adapters optimize language model policy',
                      [r'frozen|freeze|trainable|directly optimiz',r'adapters?|weights|policy|parameters']),
 'reference_policy':('参考模型与用途',r'参考(?:模型|策略)|reference (?:model|policy)',
                     'reference policy reference model log ratio logratios ref_logratios KL SFT preference objective log probability',
                     [r'reference (?:model|policy)|π\s*ref|ref_logratios',r'log|deviat|divergence|initializ|ratio']),
 'components':('架构部件变化',r'移除|去掉|哪些部件|removed|remove.{0,15}(?:component|embedding)',
               'student architecture removed embeddings pooler components', [r'remov|architecture',r'embedding|pooler|component']),
 'layers':('层数及变化',r'层数|多少层|number of layers|how many layers',
           'model architecture number layers reduced depth', [r'\blayers?\b',r'reduced|factor|depth']),
 'hidden_size':('隐藏/输出维度',r'd_?model|hidden size|hidden dimension|embedding dimension|(?:隐藏|输出|嵌入).{0,8}维度',
                'model architecture hidden size dimension dmodel embedding outputs', [r'd_?model|hidden|dimension|embedding']),
 'attention_heads':('注意力头数',r'注意力.{0,8}(?:头数|多少个?头)|多少个?(?:并行)?(?:注意力)?头|number of (?:attention )?heads|how many.{0,20}heads',
                    'multi head attention number parallel heads', [r'heads?|parallel attention']),
 'parameter_count':('模型参数量',r'参数量|参数规模|多少.{0,5}参数|number of parameters|parameter count|model size',
                    'model size total parameters number million billion largest', [r'parameters?|model size',r'million|billion|\b[MB]\b']),
 'bleu':('BLEU及任务条件',r'(?<![A-Za-z])BLEU(?![A-Za-z])',
         'translation results BLEU WMT English German French base big', [r'BLEU|WMT|English|German|French']),
 'architecture':('模型结构',r'架构|模型结构|层结构|(?:编码器|解码器)(?:的)?结构|architecture',
                 'model architecture encoder decoder layer structure self-attention feed-forward',
                 [r'architecture|(?:^|\n)\s*encoder:',r'sub.?layers?|feed.?forward|multi.?layer']),
 'attention_visibility':('注意力可见范围',
                         r'注意力.{0,8}(?:可见|可访问|掩码|范围)|(?:能|可以|允许).{0,5}(?:看到|关注).{0,10}(?:位置|词元)|attention (?:visibility|mask(?:ing)?)',
                         'encoder self-attention attend all positions bidirectional left right context decoder subsequent masking',
                         [r'self.?attention|attention mask',r'all positions|every position|bidirec-?\s*tional|left and right context|subsequent positions']),
 'model_input':('模型输入形式',
                r'输入(?:的)?(?:形式|格式|表示|构成)|input (?:format|representation|structure)',
                'input representation input sequence source tokens sentence pair embeddings position segment',
                [r'input representation|input sequence|input embeddings',r'sentence pair|pair of sentences|token sequence|symbol representations|segment embeddings']),
 'component_presence':('所问组件是否存在（区分未说明）',
                       r'是否.{0,20}(?:包含|含有|具有|具备|使用|采用|存在|保留)|是否.{0,10}沿用.{0,18}(?:编码器|解码器|注意力|\bencoder\b|\bdecoder\b)|有无.{0,20}(?:组件|编码器|解码器|注意力|层)|(?:does?|do)\b.{0,60}\b(?:include|contain|use|have|retain)\b',
                       'model architecture component includes contains uses encoder decoder attention',
                       [r'encoder|decoder|attention|component',r'include|contain|use|without|no |not |only']),
 'normalization_position':('LayerNorm位置及残差顺序',r'LayerNorm|layer\s*norm|(?:Pre|Post)[- ]?LN|层归一化',
                           'layer normalization residual connection before after sublayer Pre-LN Post-LN',
                           [r'layer.?norm|normalization',r'residual',r'before|after|inside|followed by|→']),
 'training_stability':('训练稳定性与分析范围',r'训练稳定|梯度.{0,8}(?:尺度|大小|爆炸|稳定)|training stability|gradient (?:scale|magnitude|explosion)',
                       'training stability initialization gradients theoretical analysis mean field stable unstable',
                       [r'stab|gradient',r'initialization|theor|mean.?field']),
 'warmup':('Warmup设置与适用条件',r'warm[- ]?up|预热',
           'learning rate warmup steps training remove without warm-up', [r'warm[- ]?up',r'learning rate|steps|remove']),
 'vocabulary_size':('词表大小',r'词表.{0,12}(?:大小|多少|多大|规模)|vocabulary size|how many.{0,15}(?:wordpieces|subword)',
                    'vocabulary size wordpieces subword units', [r'vocabulary|wordpiece|subword',r'\b\d[\d,]*(?:K)?\b']),
 'tokenization':('分词工具/粒度',r'分词|tokeni[sz]|(?:词表|文本).{0,12}(?:工具|编码)|BPE',
                 'tokenization vocabulary BPE SentencePiece byte-level wordpiece', [r'SentencePiece|BPE|tokeniz|wordpiece']),
 'vocabulary_shared':('输入输出词表共享',r'(?:输入|input).{0,25}(?:输出|output).{0,15}(?:共享|shar)|(?:共享|shared).{0,15}(?:词表|vocab)',
                      'vocabulary shared input output embeddings', [r'\bshared\b',r'input.{0,30}output']),
 'batch_size':('批量大小',r'batch|批量|批大小','training batch size sequences minibatch', [r'batch|mini.?batch',r'sequences']),
 'sequence_length':('最大序列/上下文长度',r'序列长度|sequence length|上下文(?:长度|窗口|容量|大小)|context (?:window|length|size)',
                     'maximum sequence length context size window tokens positional embeddings', [r'sequence length|context|maximum',r'tokens']),
 'training_steps':('训练步数',r'训练.{0,12}(?:步|steps)|步数|training steps',
                    'pre-training number steps iterations', [r'\bsteps?\b',r'iterations']),
 'epochs':('训练轮数',r'训练轮数|epochs|多少轮','training number epochs', [r'\bepochs?\b']),
 'optimizer':('优化器/超参数',r'优化器|optimizer|Adam|\bbeta\s*[_-]?[12]\b|β|权重衰减|学习率|learning rate|调度',
              'training optimizer Adam beta learning rate weight decay schedule warmup', [r'Adam|optimizer',r'beta|β|learning rate|weight decay|schedule']),
 'formula':('所问公式',r'公式|equation|formula','equation formula objective definition', [r'=|equation|formula']),
}

# Evidence-selection hints only. These match reported quantities, not particular
# papers or expected values, and do not establish semantic correctness.
VALUE_PATTERNS={
    'architecture':r'model architec-?\s*ture\s+is|encoder:\s+the encoder|(?:has|composed of|uses)\s+(?:a\s+)?(?:stack|.{0,30}sub.?layers)',
    'attention_visibility':r'attend to all positions|attend to all the positions|masking out.{0,45}subsequent|conditioning on both\s+left and right context',
    'model_input':r'input representation (?:is able|is constructed)|input sequence of symbol representations|pair of sentences',
    'objective':r'(?:masked language model|next sentence prediction|label smoothing (?:of|during)|(?:train|optimi[sz]e)\w* (?:our|the|a) .{0,65}(?:loss|objective|likelihood))',
    'layers':r'\bN\s*=\s*\d+|\b\d+[- ](?:layer|layers)\b',
    'hidden_size':r'(?:d_?model|hidden size|dimension)\s*(?:=|of|is)?\s*\d+',
    'attention_heads':r'\bh\s*=\s*\d+|\b\d+\s+(?:attention )?heads\b',
    'parameter_count':r'\b\d+(?:\.\d+)?\s*(?:million|billion|[MB])(?:\s+[A-Za-z-]+){0,2}\s+parameters?\b',
    'sequence_length':r'(?:nctx|sequence length|context (?:window|length))\s*(?:=|of|is)?\s*\d+|\b\d+\s+(?:consecutive |contiguous )?tokens\b',
    'bleu':r'BLEU\s*(?:score)?\s*(?:of|is|=)?\s*\d+',
    'parameter_updates':r'\bfrozen\b.{0,110}(?:pretrained|model|weights)|\btrainable (?:parameters|weights)|directly optimiz\w+ (?:a |the )?language model',
    'reference_policy':r'\breference (?:model|policy)\b.{0,115}(?:SFT|deviat|initializ|log)|\bdeviation from.{0,55}\breference (?:model|policy)\b',
}


def value_score(key,text):
    if key == 'sequence_length' and re.search(
        r'\b(?:nctx\s*=\s*\d+|context (?:size|length) (?:from \d+ to |of |is )\d+|'
        r'\d+[- ]tokens? context window|maximum (?:context|sequence)(?: length| size)? of \d+)\b', text, re.I,
    ):
        return 4
    return min(3,len(re.findall(VALUE_PATTERNS[key],text,re.I))) if key in VALUE_PATTERNS else 0


def reference_usage_score(text):
    """Prefer passages that connect the reference policy to the computed loss."""
    if re.search(r'(?:loss\w*|objective).{0,125}\bref_logratios\b', text, re.I | re.S):
        return 3
    if re.search(r'\bref_logratios\b|\breference (?:policy|model)\b.{0,70}\blog.{0,20}ratio', text, re.I | re.S):
        return 2
    return int(bool(re.search(r'(?:optimi[zs]e|minimi[zs]e).{0,100}\b(?:given|fixed)\s+(?:π\s*ref|reference)',
                              text, re.I | re.S)))


def intent_text(question):
    clauses=re.split(r'[，,；;。!?！？\n]',question)
    return ' '.join(clause for clause in clauses if not re.match(
        r'^\s*(?:不要(?!只)|不(?:从|用|根据|据此|做|要求|需要)|未(?:提及|描述|说明).{0,8}不等于|不得|禁止|do not|don\x27t|without)',clause,re.I))


def requested(question,limit=8):
    question=intent_text(question)
    found=[key for key,(_,pattern,_,_) in FIELDS.items() if re.search(pattern,question,re.I)]
    if 'components' in found and 'architecture' in found:found.remove('architecture')
    if 'normalization_position' in found and 'architecture' in found and not re.search(r'模型架构|整体结构|overall architecture',question,re.I):found.remove('architecture')
    # "训练硬件" is a broad field only when no explicit hardware subfield exists.
    if not set(found)&{'gpu_count','gpu_model','gpu_memory','gpu_clock','gpu_voltage'} and re.search(r'训练硬件|training hardware|hardware used',question,re.I):
        found.insert(0,'gpu_model')
    return found[:limit] if limit else found


def requested_items(question, sources):
    """Map each requested paper/field pair to the user's relevant wording."""
    keys = requested(question)
    if not keys:
        return {}
    titles = {source.get('document_id', source.get('id')): source.get('title', '') for source in sources}
    aliases = {}
    for identity, title in titles.items():
        short = title.split(':', 1)[0].strip()
        words = re.findall(r'[A-Za-z]+', short)
        acronym = ''.join(word[0].upper() for word in words) if 2 <= len(words) <= 6 else ''
        for alias in (short, acronym):
            if len(alias) >= 3:
                aliases.setdefault(alias.casefold(), set()).add(identity)
    sentences = [intent_text(sentence) for sentence in re.split(r'[。！？!?；;\n]', question)]
    items = {}
    for key in keys:
        parts = sentences
        # intent_text normalizes separators, so use the original component clause
        # to avoid assigning a decoder question to an unrelated paper mentioned nearby.
        if key == 'component_presence':
            parts = [intent_text(part) for part in re.split(r'[，,。！？!?；;\n]', question)]
        relevant = [part for part in parts if re.search(FIELDS[key][1], part, re.I)]
        by_subject = {identity: [] for identity in titles}
        for sentence in relevant:
            subjects = {next(iter(identities)) for alias, identities in aliases.items() if len(identities) == 1
                        and re.search(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])', sentence.casefold())}
            if key not in SCOPED_FIELDS or not subjects:
                subjects = titles.keys()
            for identity in subjects:
                by_subject[identity].append(sentence)
        for identity in titles:
            if by_subject[identity] or not relevant:
                items[identity, key] = ' '.join(by_subject[identity])[:400]
    return {(identity, key): items[identity, key] for identity in titles for key in keys
            if (identity, key) in items}


def requested_pairs(question, sources):
    return list(requested_items(question, sources))


def cue_score(key,text):
    return sum(bool(re.search(pattern,text,re.I|re.S)) for pattern in FIELDS[key][3])


def probe_query(question,key):
    clauses=re.split(r'[，,；;。!?！？\n]',question)
    related=[intent_text(clause) for clause in clauses if re.search(FIELDS[key][1],intent_text(clause),re.I)]
    field_scope=' '.join(related) or intent_text(question)
    question=intent_text(question)
    sections=re.findall(r'(?:第\s*|\bsection\s+)(\d+(?:\.\d+)*)(?:\s*节|\b)',question,re.I)
    variants=re.findall(r'\b[A-Za-z][A-Za-z0-9/-]*\d[A-Za-z0-9/.-]*\b',field_scope)
    phase='pre-training' if re.search(r'预训练|pre.?train',question,re.I) else 'fine-tuning' if re.search(r'微调|fine.?tun',question,re.I) else ''
    explicit=list(dict.fromkeys(re.findall(r'(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_-]{0,63}(?![A-Za-z0-9_-])',field_scope)))[:12]
    chinese_context=' '.join(re.findall(r'[\u4e00-\u9fff]+',field_scope))[:256]
    return ' '.join([*['Section '+number for number in sections],phase,*variants[:3],FIELDS[key][2],*explicit,chinese_context]).strip()


def formula_fragment(text):
    """Reject obvious broken layout fragments, not prove a formula is correct."""
    if re.search(r'=\s*\^\s*\{|=\s*$|\^\s*\{\s*\}',text):return True
    return text.count('{')!=text.count('}')


def encoder_definition(document, evidence, question):
    """Return a literal whole-model definition for a decoder question."""
    if not re.search(r'解码器|\bdecoder\b', question, re.I):
        return None
    name = document['title'].split(':', 1)[0].strip()
    if not re.fullmatch(r'[A-Za-z][\w-]{1,32}', name):
        return None
    pattern = re.compile(
        rf"\b{re.escape(name)}['’]s\s+model\s+architec(?:-\s*)?ture\s+(?:is|consists of)\s+"
        r"[\w\s-]{1,100}?\bTransformer\s+en(?:-\s*)?coder\b", re.I,
    )
    matches = {}
    identity = document.get('document_id', document.get('id'))
    for source in evidence:
        if source['document_id'] != identity or not (match := pattern.search(source['text'])):
            continue
        rest = source['text'][match.end():].lstrip()
        if rest and not re.match(r'(?:[.,;]|based\s+on\b|built\s+on\b)', rest, re.I):
            continue
        if re.search(r'\bdecoder\b', source['text'][match.start():match.end() + 140], re.I):
            continue
        quote = match.group()
        matches.setdefault(re.sub(r'-\s*\n\s*', '', quote), (quote, source['id']))
    return next(iter(matches.values())) if len(matches) == 1 else None


def interpret(payload,question,evidence,validate,*,validate_audit=None,documents=None):
    """Compile only allowlisted document/field rows; legacy callers still work."""
    keys=requested(question)
    if not keys or 'field_answers' not in payload:return None
    incoming=payload['field_answers']
    if not isinstance(incoming,list):raise ValueError('invalid_field_answers')
    by_id={item['id']:item for item in evidence}
    sources = evidence if documents is None else documents
    targets = requested_items(question, sources)
    allowed = set(targets)
    groups={};rejected=0;decisions=[]
    for index,item in enumerate(incoming[:20]):
        decision={'index':index,'status':'rejected','reason':'invalid_document_or_field'};decisions.append(decision)
        if (not isinstance(item,dict) or not isinstance(item.get('document_id'),str) or not isinstance(item.get('field'),str)
                or (item['document_id'], item['field']) not in allowed):
            rejected+=1;continue
        decision.update(document_id=item['document_id'],field=item['field'])
        key=(item['document_id'],item['field']);groups.setdefault(key,[]).append((item,decision))
    claims=[];coverage=[];missing=[]
    for document in dict.fromkeys(identity for identity, _ in targets):
        first=next(item for item in sources if item.get('document_id', item.get('id')) == document)
        title=first['title']+(' · '+first['version_label'] if first.get('version_label') else '')
        for field in keys:
            if (document, field) not in allowed:
                continue
            rows=groups.get((document,field),[]);accepted=[];states=set();invalid=False
            for item,decision in rows:
                states.add(item.get('status') if isinstance(item.get('status'),str) else 'invalid')
                if item.get('status')=='missing':
                    decision.update(status='missing_candidate',reason='provider_missing');continue
                ids=item.get('evidence_ids');text=item.get('text')
                if (item.get('status')!='answered' or not isinstance(ids,list) or not ids
                        or any(not isinstance(identity,str) or identity not in by_id or by_id[identity]['document_id']!=document for identity in ids)):
                    decision['reason']='invalid_status_or_document_evidence';invalid=True;rejected+=1;continue
                if field=='component_presence':
                    verdict=item.get('presence_status')
                    quote=item.get('support_quote')
                    decision.update(presence_status=verdict,support_quote=quote)
                    if not isinstance(verdict,str) or verdict not in {'present','absent'}:
                        decision['reason']='missing_presence_verdict';invalid=True;rejected+=1;continue
                    # Negative evidence needs an attributable explicit statement;
                    # encoder-only descriptions do not prove a decoder was removed.
                    if verdict=='absent' and (not isinstance(quote,str) or not quote.strip()
                            or not any(re.sub(r'\s+',' ',quote.strip()) in re.sub(r'\s+',' ',by_id[identity]['text']) for identity in ids)
                            or not re.search(r'\b(?:no|not|without|neither|removed?|omit(?:ted)?|exclud(?:e|ed))\b|不含|没有|未使用|移除|去掉|不使用',quote,re.I)):
                        decision['reason']='absence_without_explicit_source';invalid=True;rejected+=1;continue
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
            if field == 'component_presence' and not accepted and not invalid:
                definition = encoder_definition(first, evidence, targets[document, field])
                if definition:
                    quote, anchor = definition
                    text = f'论文把所问模型定义为 Transformer 编码器，给出的主架构未列入原解码器。原文定义「{quote}」。'
                    supported = validate({'claims':[{'text':text, 'evidence_ids':[anchor]}]}, evidence)
                    if supported:
                        accepted.append({**supported[0], 'document_id':document, 'field':field})
                        decisions.append({'document_id':document,'field':field,'status':'accepted',
                                          'reason':'encoder_architecture_definition','evidence_ids':[anchor]})
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
