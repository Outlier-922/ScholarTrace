import { useEffect, useState } from 'react';
import { ArrowDownToLine, Bell, Check, ExternalLink, LoaderCircle, Search, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import { postJSON, request } from './api';
import './literature.css';
import LiteratureBatchPanel from './LiteratureBatchPanel';
import ReferenceScreening from './ReferenceScreening';
import ReferenceTraversal from './ReferenceTraversal';
import { LocalPaperHandoff, LiteratureHandoffs } from './LiteratureHandoff';
import type { Handoff } from './LiteratureHandoff';
import type { ResearchDocument } from './types';

interface Paper { key: string; title: string; authors: string[]; identifier: string; provider: string; year: number | null; venue: string; abstract: string; landing_url: string; pdf_url: string | null; code_url?: string | null; retrieved_at: string; feedback: number; reasons: string[]; identity_checks: {identifier_exact:boolean; author_match:boolean|null; year_match:boolean|null}; change?:string; }
interface Result { background_filter?: { origin_search_id: string; excluded: {key: string; title: string; anchors: {id: string; page: number; quote: string}[]}[] }; screening?: { origin_search_id: string; goal: string; decisions: {key: string; identity: string; purpose: string; decision: string; reason: string; anchor?: {page: number; quote: string} | null}[] }; reference_discovery?: { network_requests: number; max_requests?: number; document_id: string; parse_run_id: string; rows: { unit: { id: string; text: string; sources: { page: number }[] }; status: string; lookup?: {status: string; query: string; provider: string}; candidate_keys: string[] }[] }; items: Paper[]; providers:{provider:string; status:string; cache?:string; total?:number}[]; warnings:string[]; search_id:string; }
interface Subscription { id:string; query:string; enabled:boolean; last_status:string; last_success_at:string|null; last_checked_at?:string; next_check_at:string; }
interface CheckResult { id:string; status:string; created_at:string; items:Paper[]; warnings:string[]; }
interface TrackingSnapshot { items: CheckResult[]; recommendations?: { checked_at: string; items: Paper[] } | null; }
interface RecommendationProfile { id: string; name: string; }
const statuses:Record<string,string>={ok:'已返回',failed:'请求失败',rate_limited:'被限流',not_found:'没有该标识符',cache_miss:'本地无快照',not_checked:'尚未检查',budget_exhausted:'请求预算已用完',no_results:'未返回候选',ambiguous_identifier:'多个标识符，需明确检索词',candidate_limit:'候选数量已达上限',baseline:'建立基线',updated:'发现变化',no_change:'窗口内无变化'};

export default function LiteraturePanel({onClose,onImported,initialQuery='',initialSearchId=null,parentTaskId=null,onContinue}:{parentTaskId?:string|null;onContinue:(value:Handoff)=>void;initialSearchId?:string|null;onClose:()=>void;onImported:(document?:ResearchDocument)=>void;initialQuery?:string}) {
  const [query,setQuery]=useState(initialQuery);
  const [provider,setProvider]=useState('both');
  const [mode,setMode]=useState(initialQuery?'reference':'title');
  const [author,setAuthor]=useState('');
  const [year,setYear]=useState('');
  const [result,setResult]=useState<Result|null>(null);
  const [busy,setBusy]=useState(false);
  const [loadingSnapshot,setLoadingSnapshot]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [includeDisliked,setIncludeDisliked]=useState(false);
  const [confirmed,setConfirmed]=useState<string[]>([]);
  const [importing,setImporting]=useState<string|null>(null);
  const [subscriptions,setSubscriptions]=useState<Subscription[]>([]);
  const [trackingTopic,setTrackingTopic]=useState('');
  const [trackingSnapshot,setTrackingSnapshot]=useState<{ topic: string; profile: string; value: TrackingSnapshot } | null>(null);
  const [trackingRevision,setTrackingRevision]=useState(0);
  const [showTrackingDisliked,setShowTrackingDisliked]=useState(false);
  const [trackingVote,setTrackingVote]=useState(false);
  const [verifyingCode,setVerifyingCode]=useState(false);
  const [profiles,setProfiles]=useState<RecommendationProfile[]>([{id:'default',name:'默认偏好'}]);
  const [profileId,setProfileId]=useState('default');
  const [profileName,setProfileName]=useState('');
  const [checking,setChecking]=useState<string|null>(null);
  const [tab,setTab]=useState<'search'|'tracking'|'handoff'|'batch'|'traversal'>('search');
  const [detail,setDetail]=useState<string|null>(null);
  const [trackQuery,setTrackQuery]=useState('');
  const [autoTrack,setAutoTrack]=useState(false);
  async function refreshSubscriptions(){setSubscriptions((await request<{items:Subscription[]}>('/literature/subscriptions')).items);}
  useEffect(()=>{void refreshSubscriptions().catch(()=>setError('追踪主题读取失败')); const timer=window.setInterval(()=>void refreshSubscriptions().catch(()=>{}),15000); return ()=>window.clearInterval(timer);},[]);
  useEffect(()=>{
    if(tab!=='tracking') return;
    const controller=new AbortController();
    void request<{items:RecommendationProfile[]}>('/literature/profiles',{signal:controller.signal})
      .then(value=>{if(!controller.signal.aborted) setProfiles(value.items);})
      .catch(cause=>{if(!controller.signal.aborted) setError(String(cause));});
    return ()=>controller.abort();
  },[tab]);
  const trackingLastCheck = subscriptions.find(item=>item.id===trackingTopic)?.last_checked_at;
  useEffect(()=>{
    if(!trackingTopic || tab!=='tracking') return;
    const controller=new AbortController();
    const path='/literature/subscriptions/'+trackingTopic+'/checks?include_disliked='+showTrackingDisliked+'&profile_id='+encodeURIComponent(profileId);
    void request<TrackingSnapshot>(path,{signal:controller.signal})
      .then(value=>{if(!controller.signal.aborted) setTrackingSnapshot({topic:trackingTopic,profile:profileId,value});})
      .catch(cause=>{if(!controller.signal.aborted) setError(String(cause));});
    return ()=>controller.abort();
  },[trackingTopic,trackingRevision,showTrackingDisliked,trackingLastCheck,profileId,tab]);
  useEffect(()=>{
    if(!initialSearchId)return;
    let current=true;setBusy(true);setLoadingSnapshot(true);
    void request<{request:{query:string;provider:string;mode:string;author:string;year:number|null};result:Result}>('/literature/searches/'+initialSearchId).then(saved=>{
      if(!current)return;
      setQuery(saved.request.query);setProvider(saved.request.provider);setMode(saved.request.mode);setAuthor(saved.request.author);setYear(saved.request.year?String(saved.request.year):'');setResult(saved.result);
      setNotice('已打开任务保存的检索快照，没有重新联网。核对身份并获取或关联全文后，可从“后续研究”新建任务。');
    }).catch(cause=>{if(current)setError(String(cause));}).finally(()=>{if(current){setBusy(false);setLoadingSnapshot(false);}});
    return()=>{current=false;};
  },[initialSearchId]);
  async function search(){
    setBusy(true);setError('');setNotice('');setConfirmed([]);setResult(null);
    try{setResult(await postJSON<Result>('/literature/search',{query,provider,mode,author,year:year?Number(year):null,include_disliked:includeDisliked,limit:10}));}
    catch(cause){setError(cause instanceof Error?cause.message:'检索失败');}
    finally{setBusy(false);}
  }
  async function vote(paper:Paper,value:number){
    try{await postJSON('/literature/feedback',{key:paper.key,value:paper.feedback===value?0:value});setResult(current=>current?{...current,items:current.items.map(item=>item.key===paper.key?{...item,feedback:item.feedback===value?0:value}:item)}:null);}
    catch(cause){setError(String(cause));}
  }
  async function importPaper(paper:Paper){
    setImporting(paper.key);setError('');setNotice('');
    try{const value=await postJSON<{document:ResearchDocument;handoff:Handoff}>('/literature/import',{key:paper.key,search_id:result?.search_id,parent_task_id:initialSearchId===result?.search_id?parentTaskId:null,confirm_identity:true});onImported(value.document);setTab('handoff');setNotice(value.document.status==='failed'?'文件已入库，但解析未能启动；请从文献菜单重试。':value.document.upload_outcome==='reused'?'文件已在文献库，已关联来源记录。':'PDF 已加入文献库，后台解析任务已提交。');}
    catch(cause){setError(cause instanceof Error?cause.message:'全文获取失败');}
    finally{setImporting(null);}
  }
  async function saveTopic(){
    setError('');
    try{await postJSON('/literature/subscriptions',{query:trackQuery||query,enabled:autoTrack});await refreshSubscriptions();setNotice('主题已保存；第一次成功检查只建立基线。');setTab('tracking');}
    catch(cause){setError(String(cause));}
  }
  async function checkTopic(item:Subscription){
    setChecking(item.id);setTrackingTopic(item.id);setError('');
    try{await postJSON('/literature/subscriptions/'+item.id+'/check',{});await refreshSubscriptions();}
    catch(cause){setError(String(cause));}
    finally{setChecking(null);}
  }
  async function voteTracked(paper:Paper,value:number){
    setTrackingVote(true);setError('');
    try{
      await postJSON('/literature/feedback',{key:paper.key,value:paper.feedback===value?0:value,profile_id:profileId});
      setTrackingRevision(current=>current+1);
    } catch(cause){setError(String(cause));}
    finally{setTrackingVote(false);}
  }
  async function verifyCodeLinks(){
    setVerifyingCode(true);setError('');setNotice('');
    try{
      const result=await postJSON<{items:{status:string}[];remaining:number}>('/literature/subscriptions/'+trackingTopic+'/verify-code',{});
      const licensed=result.items.filter(item=>item.status==='open_license_reported').length;
      const unavailable=result.items.filter(item=>item.status==='not_found'||item.status==='unverified'||item.status==='rate_limited').length;
      setNotice(result.items.length
        ? '核对了 '+result.items.length+' 个代码链接。'+licensed+' 个由 GitHub 标注开源许可证，'+unavailable+' 个暂无法确认。'+(result.remaining?'还有 '+result.remaining+' 个链接待核对。':'')
        : '最近一次主题检查的论文中没有披露 GitHub 代码链接。');
      setTrackingRevision(current=>current+1);
    }catch(cause){setError(String(cause));}
    finally{setVerifyingCode(false);}
  }
  async function addProfile(){
    try{
      const created=await postJSON<RecommendationProfile>('/literature/profiles',{name:profileName});
      setProfiles(current=>current.some(item=>item.id===created.id)?current:[...current,created]);
      setProfileId(created.id);setProfileName('');setError('');
    }catch(cause){setError(String(cause));}
  }
  const tracking = trackingSnapshot?.topic===trackingTopic && trackingSnapshot.profile===profileId ? trackingSnapshot.value : undefined;
  const recommendations=tracking?.recommendations;
  const checks=tracking?.items??[];
  const cards=result?.items.filter(item=>includeDisliked||item.feedback!==-1)||[];
  return <div className="modal-backdrop"><section className="literature-dialog" role="dialog" aria-modal="true" aria-label="发现与追踪"><header><div><div className="eyebrow">FOLLOW THE SOURCES</div><h2>发现与追踪</h2><p>先找到论文，再核对身份与版本。</p></div><button className="icon-button" onClick={onClose} aria-label="关闭发现与追踪"><X size={20}/></button></header>
    <div className="literature-tabs"><button className={tab==='search'?'active':''} onClick={()=>setTab('search')}><Search size={14}/>检索论文</button><button className={tab==='tracking'?'active':''} onClick={()=>{setTab('tracking');setNotice('');void refreshSubscriptions().catch(()=>setError('追踪主题读取失败'));}}><Bell size={14}/>主题追踪 <small>{subscriptions.length}</small></button><button className={tab==='traversal'?'active':''} onClick={()=>setTab('traversal')}>多层展开</button><button className={tab==='batch'?'active':''} onClick={()=>setTab('batch')}>批量入库</button><button className={tab==='handoff'?'active':''} onClick={()=>setTab('handoff')}>后续研究</button></div>
    {error&&<p className="research-error" role="alert">{error}</p>}{notice&&<p className="literature-notice" role="status"><Check size={14}/>{notice}</p>}
    {tab==='traversal'?<ReferenceTraversal onOpen={id=>{void request<{result:Result}>(`/literature/searches/${id}`).then(v=>{setResult(v.result);setConfirmed([]);setTab('search');}).catch(e=>setError(String(e)));}}/>:tab==='batch'?<LiteratureBatchPanel searchId={result?.search_id||null} parentTaskId={initialSearchId===result?.search_id?parentTaskId:null} selectedKeys={confirmed.filter(key=>cards.some(p=>p.key===key))} onContinue={onContinue} onImported={()=>onImported()}/>:tab==='handoff'?<LiteratureHandoffs onContinue={onContinue}/>:tab==='search'?<><form className="literature-search" onSubmit={event=>{event.preventDefault();void search();}}><label className="literature-query"><Search size={17}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="论文标题、参考文献、DOI 或 arXiv ID" aria-label="检索论文" maxLength={500}/><button className="primary-button" disabled={busy||query.trim().length<2}>{busy?<LoaderCircle className="spin" size={15}/>:<Search size={15}/>}检索</button></label><div className="literature-filters"><label>来源<select aria-label="文献检索来源" value={provider} onChange={event=>setProvider(event.target.value)}><option value="both">arXiv + Crossref</option><option value="arxiv">arXiv</option><option value="crossref">Crossref</option></select></label><label>方式<select aria-label="文献检索方式" value={mode} onChange={event=>setMode(event.target.value)}><option value="title">标题</option><option value="reference">参考文献</option><option value="topic">研究主题</option></select></label><label>核对作者<input aria-label="核对作者" value={author} onChange={event=>setAuthor(event.target.value)} placeholder="可选" maxLength={120}/></label><label>核对年份<input aria-label="核对年份" value={year} onChange={event=>setYear(event.target.value)} type="number" min={1600} max={2100} placeholder="可选"/></label><label className="disliked-toggle"><input type="checkbox" checked={includeDisliked} onChange={event=>setIncludeDisliked(event.target.checked)}/>显示已点踩</label></div></form>
      <div className="discovery-guidance">标识符按注册记录精确查询；标题与参考文献只返回候选。作者和年份用于显示核对结果，不会自动替你确认身份。中文查询可用，arXiv 元数据以英文为主。</div>
      {busy&&<p className="literature-notice" role="status"><LoaderCircle size={14} className="spin"/>{loadingSnapshot?'正在读取本地检索快照…':'正在查询官方元数据来源…'}</p>}{result?<>{result.background_filter && <details className="discovery-guidance">
        <summary>自动筛掉 {result.background_filter.excluded.length} 条背景导读引用</summary>
        {result.background_filter.excluded.map(item => <div key={item.key}><strong>{item.title}</strong>
          {item.anchors.map(anchor => <div key={anchor.id}><blockquote>{anchor.quote}</blockquote>
            <a href={`/api/documents/${result.reference_discovery?.document_id}/pdf#page=${anchor.page}`} target="_blank" rel="noreferrer">核对第 {anchor.page} 页原文</a>
          </div>)}
        </div>)}
        <button type="button" onClick={() => {
          void request<{result: Result}>('/literature/searches/'+result.background_filter!.origin_search_id)
            .then(saved => {setResult(saved.result);setConfirmed([]);}).catch(cause => setError(String(cause)));
        }}>查看完整原始候选</button>
      </details>}{result.reference_discovery && !result.screening && result.items.length > 0 && <ReferenceScreening key={result.search_id} searchId={result.search_id} onSaved={id => { void request<{result: Result}>(`/literature/searches/${id}`).then(v => {setResult(v.result);setConfirmed([]);}).catch(e => setError(String(e))); }} />}{result.screening && <details className="discovery-guidance"><summary>已保存的筛选轨迹：{result.screening.goal}</summary>{result.screening.decisions.map(d => <p key={d.key}>{d.key} · {({include:"采用",exclude:"排除",hold:"待查"} as Record<string,string>)[d.decision]} · 身份：{({confirmed:"已核对",mismatch:"不对应",uncertain:"待查"} as Record<string,string>)[d.identity]} · 用途：{({comparison:"比较对象",method:"方法依据",data:"数据来源",background:"背景",unknown:"未确定"} as Record<string,string>)[d.purpose]} · {d.reason}{d.anchor && <><blockquote>{d.anchor.quote}</blockquote><a href={`/api/documents/${result.reference_discovery!.document_id}/pdf#page=${d.anchor.page}`} target="_blank" rel="noreferrer">原文引用 · 第{d.anchor.page}页</a></>}</p>)}<button onClick={() => {void request<{result: Result}>(`/literature/searches/${result.screening!.origin_search_id}`).then(v => {setResult(v.result);setConfirmed([]);}).catch(e => setError(String(e)));}}>打开原候选，另存新的筛选</button><a href={`/api/literature/searches/${result.search_id}/screening-export`}>导出全部采用、排除和待查记录</a></details>}{result.reference_discovery && <details className="discovery-guidance"><summary>原文书目到候选的对应与未命中记录（本次联网请求 {result.reference_discovery.network_requests} 次）</summary>{result.reference_discovery.rows.map(row => <p key={row.unit.id}>{row.unit.text}<br/>{row.status === "candidates" ? `已保存注册记录候选 ${row.candidate_keys.length} 条，仍需核对` : row.status === "no_literal_identifier" ? "原文未提取到可用标识符" : "本地没有匹配注册记录"} {row.lookup && <span> · 查询 {row.lookup.provider}：{statuses[row.lookup.status] || row.lookup.status} · {row.lookup.query}</span>} · <a href={`/api/documents/${result.reference_discovery!.document_id}/pdf#page=${row.unit.sources[0]?.page || 1}`} target="_blank" rel="noreferrer">核对原PDF书目页</a></p>)}</details>}<div className="provider-status">{result.providers.map(item=><span className={item.status==='ok'?'ok':'failed'} key={item.provider}>{item.provider} · {statuses[item.status]||item.status}{item.total!==undefined?` · ${item.total} 条`:''}{item.cache==='hit'?' · 缓存':''}</span>)}<small>返回 {cards.length} 条候选</small></div><div className="literature-cards">{cards.map(paper=><article className="literature-card" key={paper.key}><div className="paper-meta"><span>{paper.provider}</span><span>{paper.year||'年份待核对'}</span><code>{paper.identifier}</code></div><h3><a href={paper.landing_url} target="_blank" rel="noreferrer">{paper.title}<ExternalLink size={12}/></a></h3><p className="paper-authors">{paper.authors.slice(0,12).join(' · ')}{paper.authors.length>12?' …':''}</p><p className="paper-venue">{paper.venue||'未报告刊物'} · {paper.pdf_url?'arXiv 全文可获取':'仅元数据'}</p><small className="metadata-time">来源抓取：{new Date(paper.retrieved_at).toLocaleString('zh-CN')}</small><div className="identity-checks">{paper.identity_checks.identifier_exact&&<span>标识符已解析 · 仍需核对目标</span>}{paper.identity_checks.author_match!==null&&<span className={paper.identity_checks.author_match?'match':'mismatch'}>作者{paper.identity_checks.author_match?'匹配':'不匹配'}</span>}{paper.identity_checks.year_match!==null&&<span className={paper.identity_checks.year_match?'match':'mismatch'}>年份{paper.identity_checks.year_match?'匹配':'不匹配'}</span>}</div><div className="recommendation-reasons">{paper.reasons.map(reason=><span key={reason}>{reason}</span>)}</div>{paper.abstract&&<><button className="abstract-toggle" onClick={()=>setDetail(detail===paper.key?null:paper.key)}>{detail===paper.key?'收起摘要':'查看来源摘要'}</button>{detail===paper.key&&<p className="paper-abstract">{paper.abstract}</p>}</>}<footer><div className="paper-feedback"><button aria-label={`赞同推荐 ${paper.title}`} aria-pressed={paper.feedback===1} className={paper.feedback===1?'active':''} onClick={()=>void vote(paper,1)}><ThumbsUp size={14}/></button><button aria-label={`不感兴趣 ${paper.title}`} aria-pressed={paper.feedback===-1} className={paper.feedback===-1?'active':''} onClick={()=>void vote(paper,-1)}><ThumbsDown size={14}/></button></div>{paper.pdf_url?<div className="paper-import"><label><input type="checkbox" aria-label={`已核对身份 ${paper.identifier}`} checked={confirmed.includes(paper.key)} onChange={()=>setConfirmed(current=>current.includes(paper.key)?current.filter(key=>key!==paper.key):[...current,paper.key])}/>已核对标题、作者与版本</label><button className="secondary-button" disabled={!confirmed.includes(paper.key)||!!importing} onClick={()=>void importPaper(paper)}>{importing===paper.key?<LoaderCircle size={13} className="spin"/>:<ArrowDownToLine size={13}/>}获取 PDF 入库</button></div>:<a className="text-button" href={paper.landing_url} target="_blank" rel="noreferrer">核对出版方全文 <ExternalLink size={12}/></a>}</footer><LocalPaperHandoff recordKey={paper.key} searchId={result.search_id} parentTaskId={initialSearchId===result.search_id?parentTaskId:null} onConfirmed={()=>{setTab('handoff');setNotice('本地全文交接已保存，没有重新联网。可选择已绑定全文新建研究任务。');}}/></article>)}{cards.length===0&&<p className="literature-empty">{result.background_filter ? "当前筛选后没有可入库候选。可打开筛选记录查看完整名单。" : "未显示候选。请结合来源状态区分无结果、请求失败和已点踩隐藏。"}</p>}</div><div className="research-warnings">{result.warnings.map(item=><p key={item}>{item}</p>)}</div></>:<div className="literature-empty"><Search size={30}/><h3>从标题或标识符开始</h3><p>例如：1706.03762v7、BERT，或粘贴参考文献条目。</p><p>元数据检索不依赖大模型；不会发送 PDF 正文。</p></div>}
    </>:<><div className="tracking-create"><input aria-label="追踪研究主题" value={trackQuery} onChange={event=>setTrackQuery(event.target.value)} placeholder="输入英文研究关键词，例如 graph neural networks" maxLength={300}/><label><input type="checkbox" checked={autoTrack} onChange={event=>setAutoTrack(event.target.checked)}/>启用每天检查（本机服务运行时）</label><button className="primary-button" onClick={()=>void saveTopic()} disabled={(trackQuery||query).trim().length<2}>保存主题</button></div><p className="discovery-guidance">arXiv 最近更新窗口最多 20 条。首次检查建立基线；随后区分新作品与新版本。请求失败不推进成功记录，不能保证全量覆盖。暂停后仍可手动检查。</p><div className="tracking-layout"><div className="tracking-topics">{subscriptions.map(item=><article key={item.id}><h3>{item.query}</h3><p>{item.enabled?'每天检查 · 服务运行时有效':'自动检查已暂停'} · {statuses[item.last_status]||item.last_status}</p><small>上次成功：{item.last_success_at?new Date(item.last_success_at).toLocaleString('zh-CN'):'尚无'}</small><div><button disabled={!!checking} onClick={()=>void checkTopic(item)}>{checking===item.id?<LoaderCircle size={12} className="spin"/>:<Search size={12}/>}检查更新</button><button aria-pressed={trackingTopic===item.id} onClick={()=>setTrackingTopic(item.id)}>推荐与记录</button><button onClick={()=>void postJSON('/literature/subscriptions',{query:item.query,enabled:!item.enabled}).then(refreshSubscriptions).catch(cause=>setError(String(cause)))}>{item.enabled?'暂停自动检查':'启用每天检查'}</button></div></article>)}{!subscriptions.length&&<p className="literature-empty">尚未保存追踪主题。</p>}</div>
    <div className="tracking-checks">
      <article>
        <label>推荐偏好 <select value={profileId} onChange={event=>setProfileId(event.target.value)}>
          {profiles.map(profile=><option value={profile.id} key={profile.id}>{profile.name}</option>)}
        </select></label>
        <form onSubmit={event=>{event.preventDefault();void addProfile();}}>
          <input aria-label="新偏好档案名称" placeholder="为另一位读者创建偏好" value={profileName} maxLength={60} onChange={event=>setProfileName(event.target.value)}/>
          <button type="submit" className="secondary-button" disabled={!profileName.trim()}>新建偏好档案</button>
        </form>
      </article>
      {recommendations && <article>
        <header><strong>主题推荐</strong><time>{new Date(recommendations.checked_at).toLocaleString('zh-CN')} 的 arXiv 窗口</time></header>
        <label><input type="checkbox" checked={showTrackingDisliked} onChange={event=>setShowTrackingDisliked(event.target.checked)}/>显示完整窗口及已点踩论文</label>
        <button type="button" className="secondary-button" disabled={verifyingCode} onClick={()=>void verifyCodeLinks()}>
          {verifyingCode?'正在核验代码仓库…':'核验 GitHub 代码链接'}
        </button>
        {!recommendations.items.length && <p>当前窗口没有可显示的推荐。</p>}
        {recommendations.items.map(paper=><div key={paper.key}>
          <p><a href={paper.landing_url} target="_blank" rel="noreferrer">{paper.title}</a></p>
          <ul className="recommendation-reasons">{paper.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul>
          {paper.code_url && <a href={paper.code_url} target="_blank" rel="noreferrer">查看论文声明的 GitHub 代码</a>}
          <div className="paper-feedback">
            <button type="button" disabled={trackingVote} aria-label={'赞同推荐 '+paper.title} aria-pressed={paper.feedback===1} className={paper.feedback===1?'active':''} onClick={()=>void voteTracked(paper,1)}><ThumbsUp size={14}/></button>
            <button type="button" disabled={trackingVote} aria-label={'不感兴趣 '+paper.title} aria-pressed={paper.feedback===-1} className={paper.feedback===-1?'active':''} onClick={()=>void voteTracked(paper,-1)}><ThumbsDown size={14}/></button>
          </div>
        </div>)}
      </article>}
      {checks.map(item=><article key={item.id}><header><strong>{statuses[item.status]||item.status}</strong><time>{new Date(item.created_at).toLocaleString('zh-CN')}</time></header>{item.items.map(paper=><p key={paper.key}><span>{paper.change==='baseline'?'基线':paper.change==='new_version'?'新版本':'新作品'}</span><a href={paper.landing_url} target="_blank" rel="noreferrer">{paper.title}</a><small>{paper.identifier}</small></p>)}{item.warnings.map(warning=><small className="tracking-warning" key={warning}>{warning}</small>)}</article>)}{!checks.length&&<p className="literature-empty">{trackingTopic ? "首次成功检查后显示主题推荐与记录。" : "选择一个主题查看推荐与检查记录。"}</p>}</div></div></>}
  </section></div>;
}
