import { useEffect, useRef, useState } from 'react';
import { postJSON, request } from './api';
import RegionPicker from './RegionPicker';
import './tables.css';
import type { TableReference } from './types';

type Box = [number,number,number,number];
type Method = 'native'|'scan_ocr';
interface Cell { row:number; column:number; text:string|null; bbox:Box; row_span:number; covered_by:number[]|null; ocr_confidence?:number|null; }
interface Source { id:string; document_id:string; parse_run_id:string; page:number; method?:Method; anchor?:{id:string}|null; requested_column_cuts:number[]|null; bbox:Box; crop_bbox:Box; header_bottom:number; grid:{status:string; columns:{label:string}[]; rows:(string|null)[][]; cells:Cell[]; cuts?:number[]; row_edges?:number[]; warnings:string[]; unassigned:{row:number;text:string;reason:string;bbox?:Box}[]}; }
interface Revision { id:string; rows:(string|null)[][]; labels:string[]; note:string; created_at:string; }
interface Option { block_id:string; bbox:Box; header_bottom:number; row_count:number; col_count:number; }
interface Props { documentId:string; runId:string; page:number; readOnly:boolean; title:string; onUse:(reference:TableReference)=>void; onSource:(bbox:Box,label:string)=>void; }

export default function TablePanel({documentId,runId,page,readOnly,title,onUse,onSource}:Props) {
  const [options,setOptions]=useState<Option[]>([]),[anchor,setAnchor]=useState('');
  const [box,setBox]=useState(['','','','']),[header,setHeader]=useState(''),[cuts,setCuts]=useState('');
  const [method,setMethod]=useState<Method>('native'),[markHeader,setMarkHeader]=useState(false);
  const [source,setSource]=useState<Source|null>(null),[sources,setSources]=useState<{id:string;created_at:string}[]>([]);
  const [history,setHistory]=useState<Revision[]>([]),[current,setCurrent]=useState<Revision|null>(null);
  const [rows,setRows]=useState<(string|null)[][]>([]),[labels,setLabels]=useState<string[]>([]);
  const [selected,setSelected]=useState<Cell|null>(null),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const operation=useRef<AbortController|null>(null);
  function clear(){setSource(null);setHistory([]);setCurrent(null);setRows([]);setLabels([]);setSelected(null);setNote('');}
  useEffect(()=>{
    operation.current?.abort();const abort=new AbortController();operation.current=abort;
    clear();setOptions([]);setSources([]);setAnchor('');setBox(['','','','']);setHeader('');setCuts('');setMarkHeader(false);setBusy(true);setError('');
    void Promise.all([request<{items:Option[]}>(`/documents/${documentId}/table-options?page=${page}&parse_run_id=${encodeURIComponent(runId)}`,{signal:abort.signal}),request<{items:typeof sources}>(`/documents/${documentId}/table-regions?page=${page}&parse_run_id=${encodeURIComponent(runId)}`,{signal:abort.signal})]).then(([o,s])=>{if(!abort.signal.aborted){setOptions(o.items);setSources(s.items);}}).catch(e=>{if(!abort.signal.aborted)setError(String(e));}).finally(()=>{if(!abort.signal.aborted)setBusy(false);});
    return()=>{abort.abort();operation.current?.abort();};
  },[documentId,runId,page]);
  function adopt(value:Source,revisions:Revision[]){setBox(value.bbox.map(v=>(v*100).toFixed(4)));setHeader((value.header_bottom*100).toFixed(4));setCuts(value.requested_column_cuts?.map(v=>(v*100).toFixed(4)).join(', ')||'');setAnchor(value.anchor?.id||'');setMethod(value.method||'native');setMarkHeader(false);setSource(value);setHistory(revisions);const revision=revisions[0]||null;setCurrent(revision);setRows(revision?.rows||value.grid.rows);setLabels(revision?.labels||value.grid.columns.map(c=>c.label));setSelected(null);setNote('');}
  async function act(kind:'prepare'|'load'|'save',id?:string){
    if(busy)return;operation.current?.abort();const abort=new AbortController();operation.current=abort;setBusy(true);setError('');
    try{
      if(kind==='save'&&source){
        const revision=await postJSON<Revision>(`/table-regions/${source.id}/revisions`,{rows,labels,note,parent_id:current?.id||null},abort.signal);
        if(!abort.signal.aborted){setCurrent(revision);setHistory(old=>[revision,...old]);setNote('');}
      }else{
        const values=box.map(v=>Number(v)/100),end=Number(header)/100;
        if(kind==='prepare'&&([...values,end].some(v=>!Number.isFinite(v))||box.some(v=>!v.trim())||!header.trim()))throw new Error('请填写区域与表头百分比。');
        const internal=cuts.trim()?cuts.split(/[,，\s]+/).map(v=>Number(v)/100):null;
        if(internal?.some(v=>!Number.isFinite(v)))throw new Error('列分界应为逗号分隔的百分比。');
        const value=kind==='load'?await request<Source>(`/table-regions/${id}`,{signal:abort.signal}):await postJSON<Source>('/table-regions',{document_id:documentId,parse_run_id:runId,page,bbox:values,header_bottom:end,anchor_block_id:anchor||null,column_cuts:internal,method},abort.signal);
        const revisions=await request<{items:Revision[]}>(`/table-regions/${value.id}/revisions`,{signal:abort.signal});
        if(!abort.signal.aborted){adopt(value,revisions.items);setSources(old=>old.some(s=>s.id===value.id)?old:[{id:value.id,created_at:new Date().toISOString()},...old]);}
      }
    }catch(e){if(!abort.signal.aborted)setError(e instanceof Error?e.message:String(e));}
    finally{if(!abort.signal.aborted)setBusy(false);}
  }
  function changeCell(value:string|null){if(selected)setRows(old=>old.map((row,i)=>i===selected.row?row.map((v,j)=>j===selected.column?value:v):row));}
  const c=source?.crop_bbox;const sx=(x:number)=>c?(x-c[0])/(c[2]-c[0])*1000:0;const sy=(y:number)=>c?(y-c[1])/(c[3]-c[1])*1000:0;
  const savedRows=current?.rows||source?.grid.rows;const savedLabels=current?.labels||source?.grid.columns.map(c=>c.label);
  const dirty=JSON.stringify(rows)!==JSON.stringify(savedRows)||JSON.stringify(labels)!==JSON.stringify(savedLabels);
  function useForQA(onlySelected:boolean){if(!source||source.method==='scan_ocr'||dirty)return;onUse({source_id:source.id,revision_id:current?.id||null,document_id:source.document_id,parse_run_id:source.parse_run_id,title,page:source.page,row_indices:onlySelected&&selected?[selected.row]:null});}
  const scan=method==='scan_ocr';
  const proposed=box.map(v=>Number(v)/100) as Box;
  const region=box.every(v=>v.trim())&&proposed.every(v=>Number.isFinite(v)&&v>=0&&v<=1)&&proposed[2]>proposed[0]&&proposed[3]>proposed[1]?proposed:null;
  const headerY=header.trim()?Number(header)/100:undefined;
  const canPrepare=region&&headerY!==undefined&&region[2]-region[0]>=.02&&headerY>region[1]&&headerY<region[3];
  const exported=source?`/api/table-regions/${source.id}/export?format=`:'';const revisionParam=current?`&revision_id=${current.id}`:'';
  return <section className="table-panel" aria-label="表格重建与修订"><h3>表格原图与行列核对</h3><p>第{page}页 · 在本机读取表格候选。空白格不会自动填入推断值。</p>
    <label><input type="checkbox" checked={scan} disabled={busy||readOnly} onChange={e=>{clear();setError('');setMethod(e.target.checked?'scan_ocr':'native');setAnchor('');setBox(['','','','']);setHeader('');setCuts('');setMarkHeader(false);}} />识别扫描表格</label>
    {!scan&&<label>已有表格候选<select aria-label="已有表格候选" value={anchor} disabled={busy||readOnly} onChange={e=>{clear();setAnchor(e.target.value);const o=options.find(o=>o.block_id===e.target.value);if(o){setBox(o.bbox.map(v=>(v*100).toFixed(4)));setHeader((o.header_bottom*100).toFixed(4));setCuts('');}}}><option value="">手动指定区域</option>{options.map((o,i)=><option key={o.block_id} value={o.block_id}>候选{i+1} · 原提取{o.row_count}行×{o.col_count}列</option>)}</select></label>}
    {scan&&!source&&<><p>{markHeader?'在框内点击表头与第一行数据之间。':'拖动框住所选表格的完整表头和数据，避开表题及正文。'}</p>
      {region&&<button disabled={busy||readOnly} aria-pressed={markHeader} onClick={()=>setMarkHeader(v=>!v)}>{markHeader?'返回框选表格':'标记表头下缘'}</button>}
      <RegionPicker key={`${documentId}-${runId}-${page}`} url={`/api/documents/${documentId}/table-page-image?page=${page}&parse_run_id=${encodeURIComponent(runId)}`}
        region={region} header={headerY} disabled={busy||readOnly} onChange={r=>{setBox(r?r.map(v=>(v*100).toFixed(4)):['','','','']);setHeader('');setCuts('');setError('');}}
        onPickHeader={markHeader?y=>{setHeader((y*100).toFixed(4));setMarkHeader(false);}:undefined}/>
    </>}
    {scan&&source&&<button disabled={busy||readOnly} onClick={()=>{clear();setError('');}}>重新框选或调整列界</button>}
    <details open={!anchor&&!scan}><summary>区域与列分界设置（占原页百分比）</summary><div className="table-region-fields">{['左','上','右','下'].map((name,i)=><label key={name}>{name}%<input aria-label={`表格区域${name}百分比`} type="number" min="0" max="100" step=".001" value={box[i]} disabled={busy||readOnly} onChange={e=>{clear();setAnchor('');setBox(old=>old.map((v,j)=>j===i?e.target.value:v));}}/></label>)}</div><label>表头下界%<input aria-label="表头下界百分比" type="number" value={header} step=".001" disabled={busy||readOnly} onChange={e=>{clear();setHeader(e.target.value);}}/></label><label>内部列分界%（留空时自动建议）<input aria-label="内部列分界百分比" value={cuts} disabled={busy||readOnly} placeholder="例如 35, 55, 75" onChange={e=>{clear();setCuts(e.target.value);}}/></label><small>可微调选区和表头。自动分列不准时，可填写列分界后重建。</small></details>
    {(!scan||!source)&&<button disabled={busy||readOnly||!canPrepare} onClick={()=>void act('prepare')}>{scan?'本机识别并重建行列':'按表头重建行列（不调用模型）'}</button>}
    {sources.length>0&&<select aria-label="本页表格来源历史" disabled={busy} value={source?.id||''} onChange={e=>{if(e.target.value)void act('load',e.target.value);}}><option value="">打开已保存表格来源</option>{sources.map(s=><option key={s.id} value={s.id}>{new Date(s.created_at).toLocaleString('zh-CN')}</option>)}</select>}
    {error&&<p role="alert" className="research-error">{error}</p>}{busy&&<p role="status">正在读取与重建…</p>}
    {source&&<><div className="table-crop"><img src={`/api/table-regions/${source.id}/image`} alt="所选表格区域原PDF裁剪"/><svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">{source.grid.cuts?.map((x,i)=><line key={i} x1={sx(x)} x2={sx(x)} y1={sy(source.bbox[1])} y2={sy(source.bbox[3])}/>)}<line x1={sx(source.bbox[0])} x2={sx(source.bbox[2])} y1={sy(source.header_bottom)} y2={sy(source.header_bottom)}/>{selected&&<rect className="table-selected-cell" x={sx(selected.bbox[0])} y={sy(selected.bbox[1])} width={sx(selected.bbox[2])-sx(selected.bbox[0])} height={sy(selected.bbox[3])-sy(selected.bbox[1])}/>}</svg></div>{source.grid.warnings.map(w=><p className="table-note" key={w}>{w}</p>)}
      {rows.length>0?<><p>{rows.length}个文字行 × {labels.length}列 · {scan?'扫描候选':'原生候选'}，未核验。点击单元格可编辑并定位原页。</p><div className="table-preview-scroll"><table><thead><tr>{labels.map((label,j)=><th key={j}><input aria-label={`第${j+1}列列名`} value={label} maxLength={250} disabled={busy||readOnly} onChange={e=>setLabels(old=>old.map((v,k)=>k===j?e.target.value:v))}/></th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{row.map((value,j)=>{const cell=source.grid.cells[i*labels.length+j];return cell.covered_by?null:<td key={j} rowSpan={cell.row_span} className={value!==source.grid.rows[i][j]?'manually-changed':''}><button aria-label={`编辑第${i+1}行第${j+1}列`} onClick={()=>setSelected(cell)}>{value===null?<em>未提取值</em>:value===''?<em>手工空值</em>:value}</button></td>;})}</tr>)}</tbody></table></div>
      {source.grid.unassigned.map((u,i)=><p className="table-ambiguity" key={i}>第{u.row+1}行待归列：{u.text}。{u.reason}{u.bbox&&<button onClick={()=>onSource(u.bbox!,`表格第${u.row+1}行待归列原文`)}>定位待核对原文</button>}</p>)}
      {selected&&<div className="table-cell-editor"><strong>第{selected.row+1}行 · 第{selected.column+1}列{selected.row_span>1&&` · 跨${selected.row_span}行`}</strong><textarea aria-label="选中表格单元格" value={rows[selected.row][selected.column]??''} maxLength={1000} disabled={busy||readOnly} onChange={e=>changeCell(e.target.value)}/><label><input type="checkbox" checked={rows[selected.row][selected.column]===null} disabled={busy||readOnly} onChange={e=>changeCell(e.target.checked?null:'')}/>此位置未提取值</label><button onClick={()=>onSource(selected.bbox,`表格第${selected.row+1}行第${selected.column+1}列`)}>原页定位所选单元格</button><small>{selected.ocr_confidence!=null&&`OCR 字符识别分数 ${Math.round(selected.ocr_confidence*100)}%，仍须核对。`} 原页定位指向原候选，手工改值需独立核对。</small></div>}
      <label>修订说明<input aria-label="表格修订说明" value={note} maxLength={500} disabled={busy||readOnly} onChange={e=>setNote(e.target.value)}/></label><button disabled={busy||readOnly||!note.trim()||labels.some(l=>!l.trim())} onClick={()=>void act('save')}>另存表格修订（保留原候选）</button>
      {history.length>0&&<select aria-label="表格修订历史" disabled={busy} value={current?.id||''} onChange={e=>{const r=history.find(r=>r.id===e.target.value)||null;setCurrent(r);setRows(r?.rows||source.grid.rows);setLabels(r?.labels||source.grid.columns.map(c=>c.label));setNote('');setSelected(null);}}><option value="">原始重建候选</option>{history.map(r=><option value={r.id} key={r.id}>{new Date(r.created_at).toLocaleString('zh-CN')} · 手工修订</option>)}</select>}{current&&<p>手工修订 · 未独立核验：{current.note}</p>}
      {scan?<small>扫描候选仅供原图核对、修订与导出，暂不作为问答证据。</small>:<><button disabled={busy||readOnly||dirty} onClick={()=>useForQA(false)}>整表加入问答核对</button><button disabled={busy||readOnly||dirty||!selected} onClick={()=>useForQA(true)}>仅所选行加入问答</button><small>只加入已保存版本。原生候选参与检索；手工改动独立对照，不发送给模型。{dirty&&'请先保存改动或切回原候选。'}</small></>}<div className="table-exports"><a href={exported+'csv'+revisionParam}>导出CSV</a><a href={exported+'json'+revisionParam}>导出完整来源JSON</a></div><small>CSV不保留跨行、未提取值与手工空值的区别，请同时保存来源JSON。原解析与原候选仍可回查。</small></>:<p>该区域没有原生文字。可切换到“识别扫描表格”，框选原图再试。</p>}
    </>}
  </section>;
}
