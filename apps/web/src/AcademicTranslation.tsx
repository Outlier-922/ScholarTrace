import { useEffect, useState } from 'react';
import { postJSON, request } from './api';

type Result={id?:string;title:string;source_text:string;glossary?:Record<string,string>;translation:string|null;status:string;warnings:string[];notice:string;usage:{calls:number;output_tokens:number}};

export default function AcademicTranslation(){
  const [text,setText]=useState('');
  const [title,setTitle]=useState('中文摘要英译');
  const [terms,setTerms]=useState('');
  const [result,setResult]=useState<Result|null>(null);
  const [history,setHistory]=useState<{id:string;title:string;created_at:string}[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const refresh=async()=>setHistory((await request<{items:typeof history}>('/academic-translations')).items);
  useEffect(()=>{void refresh().catch(()=>setError('暂时无法读取翻译历史。'));},[]);
  async function translate(){
    setBusy(true);setError('');setResult(null);
    try{
      const glossary:Record<string,string>={};
      for(const line of terms.split('\n').filter(line=>line.trim())){
        const split=line.indexOf('=');
        if(split<1||!line.slice(split+1).trim())throw new Error('术语格式为“中文术语=English term”，每行一条。');
        glossary[line.slice(0,split).trim()]=line.slice(split+1).trim();
      }
      setResult(await postJSON<Result>('/academic-translations',{title,text,glossary,use_model:true}));
      await refresh();
    }catch(cause){setError(cause instanceof Error?cause.message:'翻译未完成，原文仍保留。');}
    finally{setBusy(false);}
  }
  return <section>
    <h3>中文摘要 → 学术英文</h3>
    <p className="studio-note">粘贴中文摘要或学术段落，最多 4000 字符。每次至多一次模型请求，输出预留上限 6000 token；相同内容与术语可复用历史译文。全文 PDF 请使用阅读区的全文翻译。</p>
    <label className="studio-field">记录标题<input aria-label="学术英译标题" maxLength={100} disabled={busy} value={title} onChange={e=>{setTitle(e.target.value);setResult(null);}}/></label>
    <label className="studio-field">中文原文<textarea aria-label="学术英译中文原文" rows={8} maxLength={4000} disabled={busy} value={text} onChange={e=>{setText(e.target.value);setResult(null);}}/></label>
    <label className="studio-field">术语表（可选，每行 中文=English）<textarea aria-label="学术英译术语表" rows={3} disabled={busy} value={terms} onChange={e=>{setTerms(e.target.value);setResult(null);}}/></label>
    <button className="primary-button" disabled={busy||!title.trim()||text.trim().length<20} onClick={()=>void translate()}>{busy?'正在翻译…':'一键翻译为学术英文'}</button>
    {error&&<p className="studio-alert" role="alert">{error}</p>}
    <label className="studio-field">已保存译文<select aria-label="学术英译历史" disabled={busy} value={result?.id||''} onChange={e=>{const id=e.target.value;if(id){setBusy(true);setError('');void request<Result>('/academic-translations/'+id).then(setResult).catch(()=>setError('无法打开这条译文。')).finally(()=>setBusy(false));}}}><option value="">选择记录</option>{history.map(item=><option key={item.id} value={item.id}>{item.title} · {new Date(item.created_at).toLocaleString('zh-CN')}</option>)}</select></label>
    {result&&<div className="academic-translation-result"><p className="studio-note">{result.status==='cached'?'已复用历史译文，本次未新增模型调用。':result.status==='not_translated'?'当前仅保留原文。':'译文已保存，可对照原文核验。'} {result.notice}</p><h4>原文快照</h4><p style={{whiteSpace:'pre-wrap'}}>{result.source_text}</p><h4>English translation</h4><p style={{whiteSpace:'pre-wrap'}}>{result.translation||'未生成译文'}</p>{result.warnings.map(warning=><p className="studio-note" key={warning}>{warning}</p>)}{result.id&&<a className="task-export" href={'/api/academic-translations/'+result.id+'/markdown'} download>下载中英对照 Markdown</a>}</div>}
  </section>;
}
