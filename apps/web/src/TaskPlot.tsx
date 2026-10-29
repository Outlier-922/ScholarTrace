import { useEffect, useState } from 'react';
import { postJSON, request } from './api';
import { ChartControls, chartLabels, initialSpec } from './ChartControls';
import type { Dataset, Spec } from './ChartControls';
import './tasks-plot.css';

export type PlotOptions={dataset_id:string;sha256:string;spec:Spec};
export type PlotSnapshot=PlotOptions&{title:string;row_count:number;data_status:string;preview:{used_records:number;dropped_rows:number[];caption:string;warnings:string[]}};
export type PlotResult={id?:string;title?:string;data_status?:string;sha256?:string;caption?:string;used_records?:number;dropped_rows?:number[];warnings?:string[]};

export function TaskPlotPicker({onChange}:{onChange:(value:PlotOptions|null)=>void}) {
  const [datasets,setDatasets]=useState<{id:string;title:string}[]>([]);
  const [dataset,setDataset]=useState<Dataset|null>(null);
  const [spec,setSpec]=useState<Spec>(initialSpec);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{let live=true;void request<{items:{id:string;title:string}[]}>('/studio/assets?kind=dataset').then(value=>{if(live)setDatasets(value.items);}).catch(cause=>{if(live)setError(String(cause));});return()=>{live=false;};},[]);
  useEffect(()=>{onChange(!busy&&dataset&&spec.x&&spec.y.length&&spec.title.trim()?{dataset_id:dataset.id,sha256:dataset.sha256,spec}:null);},[dataset,spec,busy,onChange]);
  async function load(fetcher:()=>Promise<Dataset>) {
    setBusy(true);setError('');onChange(null);
    try {const value=await fetcher();setDataset(value);setSpec({...initialSpec,x:value.headers[0],y:[],title:value.data_status==='synthetic_demo'?'自建演示数据比较':'CSV 数据比较'});setDatasets(current=>[value,...current.filter(item=>item.id!==value.id)]);}
    catch(cause){setDataset(null);setError(cause instanceof Error?cause.message:String(cause));}
    finally{setBusy(false);}
  }
  return <section className="task-plot-picker" aria-label="任务CSV绘图规则"><h4>选择数据，再确认绘图规则</h4><p>上传的 CSV 只作为数据读取。不会从论文猜测数值、单位或实验结果。</p><div className="task-plot-toolbar"><label className="secondary-button studio-upload">导入任务 CSV<input aria-label="导入任务CSV" type="file" accept=".csv,text/csv" disabled={busy} onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file)void load(()=>{const form=new FormData();form.append('file',file);return request<Dataset>('/studio/datasets',{method:'POST',body:form});});}}/></label><button className="secondary-button" disabled={busy} onClick={()=>void load(()=>postJSON<Dataset>('/studio/datasets/demo',{}))}>加载任务自建演示 CSV</button><select aria-label="任务CSV数据" disabled={busy} value={dataset?.id||''} onChange={event=>{const id=event.target.value;if(id)void load(()=>request<Dataset>('/studio/datasets/'+id));}}><option value="">选择已有 CSV</option>{datasets.map(item=><option key={item.id} value={item.id}>{item.title} · {item.id.slice(0,8)}</option>)}</select></div>{busy&&<p role="status">正在读取本地 CSV…</p>}{error&&<p className="studio-alert" role="alert">{error}</p>}{dataset&&<><div className="studio-dataset-banner"><div><strong>{dataset.title}</strong><span>{dataset.row_count} 条记录 · {dataset.headers.length} 列</span></div><small>{dataset.data_status==='synthetic_demo'?'自建演示数据 · 非实验结果':'用户导入数据 · 真实性未验证'}</small></div><details><summary>原始 CSV 预览与哈希</summary><code>{dataset.sha256}</code><div className="studio-data-scroll"><table><thead><tr>{dataset.headers.map(name=><th key={name}>{name}</th>)}</tr></thead><tbody>{dataset.preview.map((row,index)=><tr key={index}>{row.map((cell,column)=><td key={column}>{cell}</td>)}</tr>)}</tbody></table></div></details><div className="studio-chart-config task-plot-controls"><ChartControls dataset={dataset} spec={spec} setSpec={setSpec} busy={busy}/></div><p>列和统计规则将在生成计划时校验。一个绘图步骤占用一次工具调用，不调用模型；先查看计划中的实际保留与排除记录，再开始执行。</p></>}</section>;
}

export function TaskPlotPreview({value}:{value:PlotSnapshot}) {
  return <details className="task-plot-preview" open><summary>确认 CSV 与规则 · {chartLabels[value.spec.chart_type]}</summary><strong>{value.title} · {value.data_status==='synthetic_demo'?'自建演示，非实验结果':'用户数据，真实性未验证'}</strong><p>横轴：{value.spec.x} · 指标：{value.spec.y.join('、')} · 分组：{value.spec.group_by||'无'} · 重复标识：{value.spec.repetition||'无'}</p><p>聚合：{value.spec.aggregation==='mean'?'均值':'逐条记录'} · 缺失：{value.spec.missing_policy==='drop'?'明确删除':'遇缺失停止'} · 误差条：{value.spec.error_bar} · 归一化：{value.spec.normalization}</p><p>原始 {value.row_count} 条，使用 {value.preview.used_records} 条；排除记录序号：{value.preview.dropped_rows.join('、')||'无'}</p><code>SHA-256 {value.sha256}</code><details><summary>完整规则与计算图注</summary><pre>{JSON.stringify(value.spec,null,2)}</pre><p>{value.preview.caption}</p>{value.preview.warnings.map(warning=><p key={warning}>{warning}</p>)}</details></details>;
}

export function TaskPlotResult({result}:{result:PlotResult}) {
  return <section className="task-plot-result"><p className="studio-note">{result.data_status==='synthetic_demo'?'自建演示数据 · 非论文实验结果':'用户导入 CSV · 数据真实性未验证'} · 使用 {result.used_records} 条 · 排除记录：{result.dropped_rows?.join('、')||'无'}</p><img src={'/api/studio/assets/'+result.id+'/files/figure.svg'} alt={result.title||'CSV 科研图表'}/><div className="studio-downloads">{['png','svg','pdf'].map(extension=><a key={extension} href={'/api/studio/assets/'+result.id+'/files/figure.'+extension} download>{extension.toUpperCase()}</a>)}</div><p className="studio-caption">{result.caption}</p><code>原始 CSV SHA-256 {result.sha256}</code>{result.warnings?.map(warning=><p className="studio-note" key={warning}>{warning}</p>)}<p>导出上方任务包可取得原始 CSV、处理后数据、明确规则及重画脚本。图表也保存在“写作与绘图”的历史中。</p></section>;
}
