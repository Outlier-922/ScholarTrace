import { useEffect, useState } from 'react';
import TranslationPane from './TranslationPane';
import { request } from './api';
import type { DocumentBlock, DocumentStructure, Evidence, ResearchDocument } from './types';

export type DocumentTranslationResult={document_source?:{document_id:string;parse_run_id:string;title:string;filename:string;page_count:number};counts?:Record<string,number>;total?:number;totals?:{calls:number};};

export default function TaskDocumentTranslation({result,documents,modelConfigured,onSource}:{result:DocumentTranslationResult&{id?:string};documents:ResearchDocument[];modelConfigured:boolean;onSource:(value:Evidence)=>void}){
  const [page,setPage]=useState(1);const [structure,setStructure]=useState<DocumentStructure|null>(null);const [error,setError]=useState('');
  const source=result.document_source;const document=documents.find(item=>item.id===source?.document_id);
  useEffect(()=>{if(!source)return;const controller=new AbortController();setStructure(null);setError('');void request<DocumentStructure>(`/documents/${source.document_id}/structure?page=${page}&parse_run_id=${encodeURIComponent(source.parse_run_id)}`,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setStructure(value);}).catch(cause=>{if(!controller.signal.aborted)setError(String(cause));});return()=>controller.abort();},[source?.document_id,source?.parse_run_id,page]);
  function jump(block:DocumentBlock){if(source)onSource({id:'translation_'+block.id,document_id:source.document_id,parse_run_id:source.parse_run_id,title:source.title,page:block.page,block_id:block.id,bbox:block.bbox,text:block.text,score:0});}
  if(!source||!document)return <p role="alert">全文来源暂不可用，请刷新文献库。</p>;
  return <section className="task-document-translation"><p className="studio-note">全文计划在此独立逐批执行，模型请求和预算不计入上方编排请求行；原任务等待译文处理后继续。完成全文计划后点击上方“继续未完成步骤”。</p><label className="task-document-page">查看原译对照页<input aria-label="全文对照页码" type="number" min={1} max={source.page_count} value={page} onChange={event=>setPage(Math.max(1,Math.min(source.page_count,Number(event.target.value))))}/> / {source.page_count}</label>{error&&<p role="alert">{error}</p>}{structure&&<TranslationPane document={{...document,parse_run_id:source.parse_run_id}} structure={structure} page={page} selectedBlock={null} modelConfigured={modelConfigured} linked={false} onAnchor={()=>{}} onSource={jump} initialJobId={result.id}/>}</section>;
}
