import type { Evidence, TableDraft, TableReference } from './types';
import './table-qa.css';

export function TableQAContext({items,tableOnly,onTableOnly,onRemove,activeIds}:{activeIds:string[];items:TableReference[];tableOnly:boolean;onTableOnly:(value:boolean)=>void;onRemove:(id:string)=>void}){
  if(!items.length)return null;
  return <section className="table-qa-context" aria-label="已选问答表格"><strong>已选{items.length}份表格来源 · {items.filter(r=>activeIds.includes(r.document_id)).length}份参与本次检索</strong>{items.map(item=><div key={item.source_id}><span>{item.title} · 第{item.page}页 · {item.row_indices?`文字行 ${item.row_indices.map(r=>r+1).join('、')}`:'整表'}{item.revision_id?' · 手工稿仅对照':''}{!activeIds.includes(item.document_id)&&' · 当前范围外，不参与检索'}</span><button type="button" aria-label={`移除问答表格 ${item.source_id}`} onClick={()=>onRemove(item.source_id)}>移除</button></div>)}<label><input type="checkbox" checked={tableOnly} onChange={e=>onTableOnly(e.target.checked)}/>仅检索所选表格</label><small>解析版本已固定。生成只使用原生候选，手工改动不送入模型。</small></section>;
}

export function TableDraftCards({items,onSource}:{items:TableDraft[];onSource:(e:Evidence)=>void}){
  if(!items.length)return null;
  const show=(v:string|null)=>v===null?'未提取值':v===''?'手工空值':v;
  return <section className="table-draft-results" aria-label="手工表格对照"><h4>手工稿对照 · 未核验</h4><p>以下改动没有发送给模型，也不是论文事实依据。</p>{items.map(item=><article key={item.id}><strong>{item.title} · 第{item.page}页 · 文字行{item.table_draft.row+1}</strong><p>{item.text}</p>{item.table_draft.changes.map(c=><div key={c.column}><b>{c.label}</b><div>原候选：{show(c.before)}</div><div>手工稿：{show(c.after)}</div></div>)}{item.table_draft.header_changes.map(c=><div key={`h${c.column}`}>列名手工改动：{c.before} → {c.after}</div>)}<small>修订说明：{item.table_draft.note}</small><button onClick={()=>onSource(item)}>核对原页位置</button><a href={`/api/table-regions/${item.table_draft.source_id}/export?format=json&revision_id=${item.table_draft.revision_id}`}>打开此版完整来源</a></article>)}</section>;
}
