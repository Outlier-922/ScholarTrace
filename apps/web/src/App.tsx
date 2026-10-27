import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, BookMarked, BookOpen, Check, CheckCheck, ChevronDown, ChevronRight, CircleHelp, Clock3, FilePlus2, FileText, FolderOpen, GitBranch, Layers3, Library, ListTree, LoaderCircle, Menu, MessageSquareText, MoreHorizontal, PanelLeftClose, PencilLine, Plus, RefreshCw, Search, Send, Settings2, ShieldCheck, Sparkles, Target, Trash2, Upload, X, AlertTriangle } from 'lucide-react';
import { postJSON, request } from './api';
import type { AnswerResult, DocumentBlock, DocumentStructure, Evidence, Health, ResearchDocument, Stats } from './types';

const STATUS_LABEL: Record<string, string> = { queued: '等待处理', processing: '解析中', ready: '已就绪', partial: '部分可检索', failed: '处理失败' };
const INITIAL_STATS: Stats = { document_count: 0, ready_count: 0, total_pages: 0, blocks_count: 0 };
const PROMPTS = ['这篇论文的主要方法是什么？', '训练使用了什么硬件与超参数？', '作者报告了哪些局限？'];
const PdfReader = lazy(() => import('./PdfReader'));
const ComparisonPanel = lazy(() => import('./ComparisonPanel'));
const LiteraturePanel = lazy(() => import('./LiteraturePanel'));

function Brand({ compact = false }: { compact?: boolean }) {
  return <div className={`brand ${compact ? 'compact' : ''}`}><div className="brand-mark"><BookOpen size={24} strokeWidth={1.6} /><span /></div><div><strong>研迹<span>ScholarTrace</span></strong>{!compact && <small>循证而读，沿迹而研</small>}</div></div>;
}

function formatDate(value: string) {
  const date = new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

export default function App() {
  const [documents, setDocuments] = useState<ResearchDocument[]>([]);
  const [stats, setStats] = useState<Stats>(INITIAL_STATS);
  const [health, setHealth] = useState<Health | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const [structure, setStructure] = useState<DocumentStructure | null>(null);
  const [selectedBlock, setSelectedBlock] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [viewParseRun, setViewParseRun] = useState<string | null>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [literatureOpen, setLiteratureOpen] = useState(false);
  const [literatureQuery, setLiteratureQuery] = useState('');
  const [libraryQuery, setLibraryQuery] = useState('');
  const [outlineTab, setOutlineTab] = useState<'library' | 'outline'>('library');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<AnswerResult | null>(null);
  const [askedQuestion, setAskedQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [connectionError, setConnectionError] = useState('');
  const [notice, setNotice] = useState<{ text: string; kind: 'success' | 'error' | 'info' } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [modal, setModal] = useState<'roadmap' | 'settings' | 'help' | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [sideCollapsed, setSideCollapsed] = useState(false);
  const [menuDoc, setMenuDoc] = useState<string | null>(null);
  const [deleteDoc, setDeleteDoc] = useState<ResearchDocument | null>(null);
  const [renameDoc, setRenameDoc] = useState<ResearchDocument | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [reprocessDoc, setReprocessDoc] = useState<ResearchDocument | null>(null);
  const [reprocessOcr, setReprocessOcr] = useState(true);
  const [reprocessTables, setReprocessTables] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [qaScope, setQaScope] = useState<'current' | 'library'>('current');
  const [expandedEvidence, setExpandedEvidence] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const answerRef = useRef<HTMLDivElement>(null);
  const aliveRef = useRef(true);
  const askControllerRef = useRef<AbortController | null>(null);
  const pendingEvidenceRef = useRef<Evidence | null>(null);

  const selectedDocument = documents.find((item) => item.id === selectedId) ?? null;
  const ocrReady = health?.capabilities.ocr_ready === true;
  const ocrSupported = health?.capabilities.ocr_supported === true;
  const historicalRun = !!(viewParseRun && selectedDocument?.parse_run_id && viewParseRun !== selectedDocument.parse_run_id);
  const availableDocuments = documents.filter((item) => item.status === 'ready' || item.status === 'partial');
  const processingCount = documents.filter((item) => item.status === 'queued' || item.status === 'processing').length;
  const visibleDocuments = documents.filter((item) => `${item.title} ${item.filename} ${item.authors?.join(' ')}`.toLowerCase().includes(libraryQuery.toLowerCase()));
  const contextIds = checkedIds.length ? checkedIds.filter((id) => availableDocuments.some((item) => item.id === id)) : qaScope === 'library' ? availableDocuments.map((item) => item.id) : selectedDocument && availableDocuments.some((item) => item.id === selectedDocument.id) ? [selectedDocument.id] : [];

  const refresh = useCallback(async () => {
    try {
      const [library, currentStats, status] = await Promise.all([
        request<{ items: ResearchDocument[] }>('/documents'),
        request<Stats>('/stats'),
        request<Health>('/health'),
      ]);
      if (!aliveRef.current) return;
      setDocuments(library.items);
      setStats(currentStats);
      setHealth(status);
      setConnectionError('');
      setSelectedId((current) => library.items.some((item) => item.id === current) ? current : library.items[0]?.id ?? null);
      setCheckedIds((current) => current.filter((id) => library.items.some((item) => item.id === id)));
    } catch (cause) {
      if (aliveRef.current) setConnectionError(cause instanceof Error ? cause.message : '无法连接后端服务');
    } finally {
      if (aliveRef.current) setInitialLoading(false);
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => { aliveRef.current = false; window.clearInterval(timer); askControllerRef.current?.abort(); };
  }, [refresh]);

  useEffect(() => {
    const target = pendingEvidenceRef.current;
    setCurrentPage(target?.document_id === selectedId ? target.page : 1);
    setSelectedBlock(target?.document_id === selectedId ? target.block_id : null);
    setViewParseRun(target?.document_id === selectedId ? target.parse_run_id || null : null);
    pendingEvidenceRef.current = null;
    setStructure(null);
  }, [selectedId]);

  useEffect(() => { setStructure(null); }, [viewParseRun]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        const runQuery = viewParseRun ? `&parse_run_id=${encodeURIComponent(viewParseRun)}` : '';
        const next = await request<DocumentStructure>(`/documents/${selectedId}/structure?page=${currentPage}${runQuery}`, { signal: controller.signal });
        if (!controller.signal.aborted) setStructure(next);
      } catch (cause) {
        if (!controller.signal.aborted && selectedDocument?.status === 'ready') {
          setNotice({ text: cause instanceof Error ? cause.message : '结构加载失败', kind: 'error' });
        }
      }
    };
    void load();
    const timer = selectedDocument?.status === 'processing' || selectedDocument?.status === 'queued' ? window.setInterval(() => void load(), 10000) : undefined;
    return () => { controller.abort(); if (timer) window.clearInterval(timer); };
  }, [selectedId, selectedDocument?.status, selectedDocument?.parse_run_id, currentPage, viewParseRun]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), notice.kind === 'error' ? 9000 : 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !mutating) { setModal(null); setMenuDoc(null); setDeleteDoc(null); setRenameDoc(null); setReprocessDoc(null); setMobileNav(false); setComparisonOpen(false); setLiteratureOpen(false); }
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [mutating]);

  async function uploadFiles(files: FileList | File[]) {
    const array = Array.from(files);
    const pdfs = array.filter((file) => file.name.toLowerCase().endsWith('.pdf'));
    if (!pdfs.length) { setNotice({ text: '请选择 PDF 文件。', kind: 'error' }); return; }
    if (pdfs.some((file) => file.size > 100 * 1024 * 1024)) { setNotice({ text: '单个文件请控制在 100 MB 以内。', kind: 'error' }); return; }
    const body = new FormData();
    pdfs.forEach((file) => body.append('files', file));
    setUploading(true);
    setDragging(false);
    try {
      const response = await request<{ items: ResearchDocument[]; errors?: { filename: string; error?: string; message?: string }[] }>('/documents', { method: 'POST', body });
      await refresh();
      if (response.items.length) {
        setSelectedId(response.items[0].id);
        setOutlineTab('library');
      }
      if (response.errors?.length) {
        setNotice({ text: `已接收 ${response.items.length} 份文件；${response.errors.map((item) => `${item.filename}：${item.message || item.error || '文件无法处理'}`).join('；')}`, kind: 'error' });
      } else if (response.items.some((item) => item.duplicate_kind === 'exact_file')) {
        setNotice({ text: '检测到相同文件，已打开库中已有文献，避免重复入库。', kind: 'info' });
      } else {
        setNotice({ text: `已接收 ${response.items.length} 份 PDF，正在逐页解析。${pdfs.length !== array.length ? '非 PDF 文件已跳过。' : ''}`, kind: 'success' });
      }
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : '上传失败，请重试', kind: 'error' });
    } finally { setUploading(false); if (inputRef.current) inputRef.current.value = ''; }
  }

  async function removeDocument() {
    if (!deleteDoc) return;
    setMutating(true);
    try {
      await request(`/documents/${deleteDoc.id}`, { method: 'DELETE' });
      if (answer?.evidence.some((item) => item.document_id === deleteDoc.id)) setAnswer(null);
      setNotice({ text: '文献已从当前工作区删除。', kind: 'success' });
      setDeleteDoc(null);
      await refresh();
    } catch (cause) { setNotice({ text: cause instanceof Error ? cause.message : '删除失败', kind: 'error' }); }
    finally { setMutating(false); }
  }

  async function renameDocument() {
    if (!renameDoc || !renameTitle.trim()) return;
    setMutating(true);
    try {
      await request(`/documents/${renameDoc.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: renameTitle.trim() }) });
      setRenameDoc(null);
      await refresh();
      setNotice({ text: '文献标题已更新。', kind: 'success' });
    } catch (cause) { setNotice({ text: cause instanceof Error ? cause.message : '更新失败', kind: 'error' }); }
    finally { setMutating(false); }
  }

  async function askQuestion() {
    if (!question.trim() || asking || !contextIds.length) return;
    const currentQuestion = question.trim();
    setAskedQuestion(currentQuestion);
    setAsking(true);
    setAnswer(null);
    setExpandedEvidence([]);
    const controller = new AbortController();
    askControllerRef.current = controller;
    try {
      const result = await postJSON<AnswerResult>('/ask', { question: currentQuestion, document_ids: contextIds, limit: 6 }, controller.signal);
      if (!controller.signal.aborted) { setAnswer(result); setQuestion(''); }
    } catch (cause) { if (!controller.signal.aborted) setNotice({ text: cause instanceof Error ? cause.message : '检索失败，请重试', kind: 'error' }); }
    finally { if (!controller.signal.aborted) setAsking(false); }
  }

  function openReprocess(document: ResearchDocument) {
    setMenuDoc(null);
    setReprocessDoc(document);
    setReprocessOcr(ocrReady);
    setReprocessTables(health?.capabilities.structured_tables === true);
  }

  async function reprocessDocument() {
    if (!reprocessDoc || mutating || (reprocessOcr && !ocrReady)) return;
    const documentId = reprocessDoc.id;
    setMutating(true);
    try {
      await postJSON<ResearchDocument>(`/documents/${documentId}/reprocess`, { ocr: reprocessOcr, tables: reprocessTables });
      setReprocessDoc(null);
      if (selectedId === documentId) { setViewParseRun(null); setSelectedBlock(null); setStructure(null); }
      await refresh();
      setNotice({ text: '已创建新的解析记录。原文件与历史证据保留，处理进度会自动更新。', kind: 'success' });
    } catch (cause) { setNotice({ text: cause instanceof Error ? cause.message : '无法开始重新解析', kind: 'error' }); }
    finally { setMutating(false); }
  }

  function jumpToEvidence(evidence: Evidence) {
    if (!documents.some((item) => item.id === evidence.document_id)) { setNotice({ text: '该证据的源文献已不在当前工作区。', kind: 'error' }); return; }
    if (selectedId !== evidence.document_id) {
      pendingEvidenceRef.current = evidence;
      setSelectedId(evidence.document_id);
    } else { setViewParseRun(evidence.parse_run_id || null); setCurrentPage(evidence.page); setSelectedBlock(evidence.block_id); }
  }

  function selectBlock(block: DocumentBlock) {
    setCurrentPage(block.page);
    setSelectedBlock(block.id);
  }

  return (
    <div className={`app-shell ${sideCollapsed ? 'sidebar-collapsed' : ''}`}>
      <input ref={inputRef} type="file" accept="application/pdf,.pdf" multiple className="visually-hidden" onChange={(event) => { if (event.target.files) void uploadFiles(event.target.files); }} aria-label="选择 PDF 文件" />
      {mobileNav && <div className="nav-backdrop" onClick={() => setMobileNav(false)} />}
      <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
        <Brand />
        <div className="workspace-picker"><span className="workspace-avatar">研</span><div><strong>我的研究空间</strong><small>本地工作区</small></div><FolderOpen size={15} /></div>
        <div className="nav-label">研究工作台</div>
        <nav className="primary-nav" aria-label="主要导航">
          <button onClick={() => { setLiteratureQuery(''); setLiteratureOpen(true); setMobileNav(false); }} title="发现与追踪"><Search size={18} /><span>发现与追踪</span></button>
          <button className="active" onClick={() => { setModal(null); setMobileNav(false); }}><Library size={18} /><span>文献与阅读</span><span className="nav-count">{documents.length}</span></button>
          <button onClick={() => { setModal('roadmap'); setMobileNav(false); }}><GitBranch size={18} /><span>引用图谱</span><small>待接入</small></button>
          <button onClick={() => { setModal('roadmap'); setMobileNav(false); }}><PencilLine size={18} /><span>写作与绘图</span><small>待接入</small></button>
        </nav>
        <div className="sidebar-divider" />
        <div className="nav-label">证据工作流</div>
        <div className="workflow-nav"><div><span className={`workflow-step ${documents.length ? 'completed' : ''}`}>1</span><span>导入与解析</span>{documents.length > 0 && <Check size={13} />}</div><i /><div><span className={`workflow-step ${selectedDocument?.progress_pages ? 'completed' : ''}`}>2</span><span>原文与定位</span>{!!selectedDocument?.progress_pages && <Check size={13} />}</div><i /><div><span className={`workflow-step ${contextIds.length ? 'current' : ''}`}>3</span><span>检索与核验</span>{contextIds.length > 0 && <span className="tiny-dot" />}</div></div>
        <div className="sidebar-note"><ShieldCheck size={19} /><strong>让每个结论有出处</strong><p>从真实文献出发，保留版本、页码和原文位置。</p><button onClick={() => setModal('help')}>了解证据模式 <ArrowRight size={13} /></button></div>
        <div className="sidebar-bottom"><button onClick={() => setModal('settings')}><Settings2 size={17} /><span>连接与配置</span></button><button onClick={() => setModal('help')}><CircleHelp size={17} /><span>使用指南</span></button><div className="local-user"><span>研</span><div><strong>个人研究者</strong><small>开发预览 · v{health?.version || '0.4.0'}</small></div><span className={`connection-dot ${connectionError ? 'offline' : ''}`} title={connectionError ? '后端未连接' : '本地服务已连接'} /></div></div>
      </aside>

      <main className="main-area">
        <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" onClick={() => setMobileNav(true)} aria-label="展开导航"><Menu size={20} /></button><button className="icon-button desktop-collapse" onClick={() => setSideCollapsed(!sideCollapsed)} title={sideCollapsed ? '展开导航' : '收起导航'} aria-label="切换导航"><PanelLeftClose size={17} /></button><span>我的研究空间</span><ChevronRight size={14} /><strong>文献与阅读</strong></div><div className="topbar-actions"><span className="phase-pill"><span />阶段 04 · 发现与追踪</span><button className="text-button roadmap-button" onClick={() => setModal('roadmap')}>开发路线 <ArrowRight size={14} /></button></div></header>
        <div className="main-content">
          <section className="page-intro"><div><div className="eyebrow"><span />YOUR RESEARCH, WITH EVIDENCE</div><h1>让阅读，成为研究的起点<span>。</span></h1><p>把论文留在一处，让问题回到原文。</p></div><button className="primary-button" disabled={uploading} onClick={() => inputRef.current?.click()}>{uploading ? <LoaderCircle className="spin" size={17} /> : <Plus size={18} />} {uploading ? '正在上传' : '导入文献'}<kbd>PDF</kbd></button></section>

          {connectionError && <div className="connection-banner" role="alert"><AlertTriangle size={17} /><span>后端尚未连接：{connectionError}。请启动 API 服务后重试。</span><button onClick={() => void refresh()}><RefreshCw size={14} />重新连接</button></div>}

          <section className="stats-row" aria-label="工作区统计"><div><span className="stat-icon sage"><BookMarked size={19} /></span><div><span>文献库</span><strong>{stats.document_count}<small>篇文献</small></strong></div><span className="stat-detail">{stats.ready_count} 篇已就绪</span></div><div><span className="stat-icon sand"><Layers3 size={19} /></span><div><span>原文页面</span><strong>{stats.total_pages.toLocaleString()}<small>页</small></strong></div><span className="stat-detail">按页解析</span></div><div><span className="stat-icon lavender"><Target size={19} /></span><div><span>可检索内容</span><strong>{stats.blocks_count.toLocaleString()}<small>个内容块</small></strong></div><span className="stat-detail">附原文坐标</span></div></section>

          <div className="workbench-heading"><div><h2>阅读工作台</h2><button className="secondary-button compare-entry" onClick={() => setComparisonOpen(true)} disabled={availableDocuments.length < 2}>多文对比</button><span className="subtle-tag">{processingCount ? `${processingCount} 份正在处理` : '原文 · 证据 · 问题'}</span></div><div className="evidence-status"><span className={`status-dot ${health?.model_configured ? '' : 'amber'}`} />{health?.model_configured ? '模型已配置' : '本地证据检索模式'}<button onClick={() => setModal('settings')} title="查看模型连接说明" aria-label="查看模型连接说明"><CircleHelp size={14} /></button></div></div>

          <section className="workbench">
            <aside className="document-panel">
              <div className="document-tabs"><button className={outlineTab === 'library' ? 'active' : ''} onClick={() => setOutlineTab('library')}><Library size={15} />文献库<span>{documents.length}</span></button><button className={outlineTab === 'outline' ? 'active' : ''} onClick={() => setOutlineTab('outline')}><ListTree size={15} />目录</button></div>
              {outlineTab === 'library' ? <>
                <label className="library-search"><Search size={15} /><input placeholder="搜索标题或文件名" value={libraryQuery} onChange={(event) => setLibraryQuery(event.target.value)} aria-label="搜索文献" />{libraryQuery && <button onClick={() => setLibraryQuery('')} aria-label="清除搜索"><X size={13} /></button>}</label>
                <div className={`compact-upload ${dragging ? 'dragging' : ''}`} role="button" tabIndex={0} onClick={() => !uploading && inputRef.current?.click()} onKeyDown={(event) => { if (event.key === 'Enter') inputRef.current?.click(); }} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); if (!uploading) void uploadFiles(event.dataTransfer.files); }}><Upload size={18} /><span>{uploading ? '正在接收文件…' : dragging ? '松开以导入文献' : '拖入 PDF，或点击上传'}</span><small>支持多文件 · 单个不超过 100 MB</small></div>
                <div className="library-caption"><span>{libraryQuery ? `找到 ${visibleDocuments.length} 篇` : '全部文献'}</span>{checkedIds.length > 0 ? <button onClick={() => setCheckedIds([])}>清除已选 {checkedIds.length}</button> : <span>勾选以跨文检索</span>}</div>
                <div className="document-list">
                  {initialLoading ? <div className="panel-empty"><LoaderCircle className="spin" size={21} /><p>正在连接文献库</p></div> : !visibleDocuments.length ? <div className="panel-empty"><FolderOpen size={30} strokeWidth={1.3} /><strong>{libraryQuery ? '没有匹配的文献' : '你的研究从这里开始'}</strong><p>{libraryQuery ? '试试其他标题关键词' : '导入第一篇 PDF，建立自己的证据库。'}</p></div> : visibleDocuments.map((item) => <div className={`document-card ${item.id === selectedId ? 'selected' : ''}`} key={item.id}>
                    <div className="document-card-top"><label className="checkbox-wrap"><input type="checkbox" checked={checkedIds.includes(item.id)} onChange={() => setCheckedIds((current) => current.includes(item.id) ? current.filter((id) => id !== item.id) : [...current, item.id])} aria-label={`将 ${item.title} 加入跨文检索`} /><span /></label><span className={`status-label ${item.status}`}>{item.status === 'processing' && <LoaderCircle className="spin" size={11} />}{STATUS_LABEL[item.status] || item.status}</span><div className="doc-menu-wrap"><button className="icon-button" onClick={() => setMenuDoc(menuDoc === item.id ? null : item.id)} aria-label={`${item.title} 的操作`}><MoreHorizontal size={16} /></button>{menuDoc === item.id && <div className="doc-menu"><button disabled={item.status === 'processing' || item.status === 'queued'} onClick={() => openReprocess(item)}><RefreshCw size={13} />重新解析</button><button onClick={() => { setRenameDoc(item); setRenameTitle(item.title); setMenuDoc(null); }}><PencilLine size={13} />编辑标题</button><a href={`/api/documents/${item.id}/pdf`} target="_blank" rel="noreferrer" onClick={() => setMenuDoc(null)}><ArrowDownToLine size={13} />打开原文件</a><button className="danger" onClick={() => { setDeleteDoc(item); setMenuDoc(null); }}><Trash2 size={13} />删除文献</button></div>}</div></div>
                    <button className="document-card-main" onClick={() => { setSelectedId(item.id); setMenuDoc(null); }}><div className="document-format"><FileText size={18} /><span>PDF</span></div><h3>{item.title || item.filename}</h3><p title={item.filename}>{item.authors?.length ? item.authors.slice(0, 2).join(' · ') : item.filename}</p><div className="document-meta"><span>{item.page_count || '—'} 页</span>{item.version_label && <span className="version-tag">{item.version_label}</span>}<time>{formatDate(item.created_at)}</time></div>{(item.status === 'processing' || item.status === 'queued') && <div className="document-progress"><div><span style={{ width: `${item.page_count ? (item.progress_pages / item.page_count) * 100 : 3}%` }} /></div><small>{item.progress_pages || 0} / {item.page_count || '—'} 页</small></div>}</button>
                    {item.duplicate_of && <div className="duplicate-note"><Layers3 size={12} /><span>{item.version_recommendation || '检测到内容重合，原文件已保留'}</span></div>}
                  </div>)}
                </div>
              </> : <div className="outline-panel"><div className="outline-intro"><ListTree size={17} /><strong>文档结构</strong><span>{structure?.outline.length || 0} 个标题</span></div>{!selectedDocument ? <div className="panel-empty"><ListTree size={28} /><p>导入并选择一篇文献后，<br />这里会显示标题导航。</p></div> : !structure?.outline.length ? <div className="panel-empty"><ListTree size={28} /><p>{selectedDocument.status === 'processing' ? '正在识别标题结构…' : '未识别到可靠的标题层级。可通过页码和结构文本阅读。'}</p></div> : <div className="outline-list">{structure.outline.map((item) => <button key={item.id} className={selectedBlock === item.block_id ? 'active' : ''} style={{ paddingLeft: `${12 + (Math.min(item.level, 4) - 1) * 12}px` }} onClick={() => { setCurrentPage(item.page); setSelectedBlock(item.block_id); }}><span>{item.title}</span><small>{item.page}</small></button>)}</div>}</div>}
              <div className="document-panel-footer"><ShieldCheck size={13} />原始文件在本机持久保存</div>
            </aside>

            <div className="reader-panel">
              {selectedDocument ? <>
                {historicalRun && <div className="historical-run-banner"><Clock3 size={14} /><span>当前显示历史解析记录；新问题将检索最新解析。</span><button onClick={() => { setViewParseRun(null); setSelectedBlock(null); setStructure(null); }}>返回最新解析</button></div>}{selectedDocument.warnings?.length > 0 && <div className="document-warning"><AlertTriangle size={14} /><p>{selectedDocument.warnings.join('；')}</p></div>}
                <Suspense fallback={<div className="reader-error"><LoaderCircle className="spin" size={25} /><p>正在加载 PDF 阅读引擎</p></div>}><PdfReader onFindReference={text => { setLiteratureQuery(text.slice(0, 500)); setLiteratureOpen(true); }} modelConfigured={health?.model_configured === true} ocrReady={ocrReady} onRequestReprocess={() => openReprocess(selectedDocument)} document={selectedDocument} structure={structure} page={currentPage} onPage={(page) => { setCurrentPage(page); setSelectedBlock(null); }} selectedBlock={selectedBlock} onSelectBlock={selectBlock} /></Suspense>
              </> : <div className={`reader-welcome ${dragging ? 'dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); void uploadFiles(event.dataTransfer.files); }}><div className="welcome-top"><BookOpen size={17} /><span>原文阅读区</span><span className="welcome-top-end">PDF · STRUCTURED TEXT</span></div><div className="welcome-center"><div className="paper-illustration" aria-hidden="true"><div className="paper-back" /><div className="paper-front"><div className="paper-mini-label">RESEARCH NOTES</div><div className="paper-heading" /><div className="paper-line" /><div className="paper-line short" /><div className="paper-figure"><i /><i /><i /><i /><i /></div><div className="paper-columns"><div /><div /></div></div><span className="illustration-seal"><ShieldCheck size={22} strokeWidth={1.6} /></span></div><div className="eyebrow">START WITH A PAPER</div><h2>下一次发现，<br />从读懂一篇论文开始。</h2><p>导入 PDF，在原文中阅读、定位与提问。<br />每一条证据，都保留它的来处。</p><button className="primary-button" onClick={() => inputRef.current?.click()} disabled={uploading}><FilePlus2 size={17} />导入第一篇文献<ArrowRight size={15} /></button><span className="welcome-hint">也可以将 PDF 直接拖到这里</span></div><div className="welcome-bottom"><div><ListTree size={16} /><span>结构化目录</span></div><div><Target size={16} /><span>原文定位</span></div><div><MessageSquareText size={16} /><span>证据检索</span></div></div></div>}
            </div>

            <aside className="assistant-panel">
              <div className="assistant-heading"><div className="assistant-icon"><Sparkles size={17} /></div><div><h2>循证助手</h2><p>{health?.model_configured ? '基于检索证据生成回答' : '先找证据，再形成判断'}</p></div><span className="assistant-live" title={health?.model_configured ? '模型回答模式' : '本地检索模式'} /></div>
              <div className="assistant-context"><span><FileText size={13} />{checkedIds.length ? `已勾选 ${contextIds.length} 篇可检索文献` : qaScope === 'library' ? `整个文献库 · ${contextIds.length} 篇` : selectedDocument ? historicalRun ? '当前文献 · 检索最新解析' : '当前文献' : '尚未选择文献'}</span>{!checkedIds.length && <select aria-label="选择问答范围" value={qaScope} onChange={(event) => setQaScope(event.target.value as 'current' | 'library')}><option value="current">当前文献</option><option value="library">整个文献库</option></select>}{checkedIds.length > 0 && <button onClick={() => setCheckedIds([])}>清除</button>}</div>
              <div className="assistant-body" ref={answerRef}>
                {!answer && !asking ? <div className="assistant-welcome"><div className="assistant-welcome-icon"><MessageSquareText size={23} strokeWidth={1.5} /></div><h3>带着问题读论文</h3><p>检索文献中的相关段落，<br />点击证据即可回到原文。</p><div className="assistant-mode-note"><ShieldCheck size={15} /><span>{health?.model_configured ? '回答使用当前文献证据，仍需核验原文。' : '当前未配置大模型。提供原文检索结果，不生成未经核验的结论。'}</span></div><span className="prompt-label">从一个问题开始</span><div className="suggested-prompts">{PROMPTS.map((prompt) => <button key={prompt} onClick={() => { setQuestion(prompt); questionRef.current?.focus(); }}><span>{prompt}</span><ArrowRight size={13} /></button>)}</div><div className="evidence-guide"><span className="guide-line" /><span>答案的起点，始终是原文</span><span className="guide-line" /></div></div> : <>
                  <div className="asked-question"><span>你的问题</span><p>{askedQuestion}</p></div>
                  {asking ? <div className="assistant-thinking" role="status"><LoaderCircle className="spin" size={18} /><div><strong>正在寻找原文证据</strong><p>检索内容块，并检查可引用的位置…</p></div></div> : answer && <div className="answer-result"><div className="answer-mode"><ShieldCheck size={13} />{answer.mode === 'llm' ? '基于证据的回答' : '原文证据检索'}<span className="retrieval-mode-label" title="当前实际执行的检索方式；相似度不代表事实正确率">{answer.retrieval?.mode === 'bm25_dense_rrf' ? 'BM25 + 语义融合' : 'BM25'}</span></div><div className="answer-copy">{answer.answer}</div>{answer.warnings?.length > 0 && <div className="answer-warning">{answer.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div>}{answer.missing_information?.length > 0 && <div className="missing-information"><AlertTriangle size={13} /><div><strong>需要进一步核验</strong>{answer.missing_information.map((item, index) => <p key={index}>{item}</p>)}</div></div>}<div className="evidence-list-heading"><span>原文证据</span><small>{answer.evidence.length} 条 · 点击定位</small></div><div className="evidence-list">{answer.evidence.map((evidence, index) => <article className={`evidence-card ${selectedBlock === evidence.block_id && selectedId === evidence.document_id ? 'active' : ''}`} key={evidence.id}><button className="evidence-source" onClick={() => jumpToEvidence(evidence)}><span className="evidence-number">{String(index + 1).padStart(2, '0')}</span><strong title={evidence.title}>{evidence.title}</strong><ArrowRight size={14} /></button><p className={expandedEvidence.includes(evidence.id) ? 'expanded' : ''}>{evidence.text}</p>{evidence.text.length > 320 && <button className="expand-evidence" onClick={() => setExpandedEvidence((current) => current.includes(evidence.id) ? current.filter((id) => id !== evidence.id) : [...current, evidence.id])}>{expandedEvidence.includes(evidence.id) ? '收起' : '展开摘录'}<ChevronDown size={12} /></button>}<button className="evidence-location" onClick={() => jumpToEvidence(evidence)}><Target size={12} /><span title={evidence.extraction === 'rapidocr' ? 'OCR 引擎识别分数（0—1），非准确率，请核对原图' : undefined}>第 {evidence.page} 页{evidence.version_label ? ` · ${evidence.version_label}` : ''}{evidence.extraction === 'rapidocr' ? ` · OCR${typeof evidence.confidence === 'number' ? ` 识别分数 ${Math.max(0, Math.min(1, evidence.confidence)).toFixed(2)}` : ''}` : ''}</span><span>查看原文 ↗</span></button></article>)}</div>{answer.steps?.length > 0 && <details className="trace-details"><summary><CheckCheck size={14} />查看执行轨迹<span>{answer.steps.length} 步</span></summary><ol>{answer.steps.map((step, index) => <li key={index}><span className={`trace-dot ${step.status === 'failed' ? 'failed' : ''}`} /><strong>{step.label}</strong><p>{step.detail}</p></li>)}</ol></details>}</div>}
                </>}
              </div>
              <form className="question-composer" onSubmit={(event) => { event.preventDefault(); void askQuestion(); }}><textarea ref={questionRef} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={contextIds.length ? '向文献提问，寻找原文依据…' : '导入文献后，在这里提出问题…'} aria-label="向文献提问" rows={3} maxLength={2000} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void askQuestion(); } }} /><div className="composer-bottom"><span>{asking ? '正在检索证据…' : `${contextIds.length} 篇文献 · Enter 发送`}</span><button type="submit" disabled={!question.trim() || asking || !contextIds.length || !!connectionError} aria-label="发送问题" title={!contextIds.length ? '请先导入可检索文献' : '发送问题'}>{asking ? <LoaderCircle className="spin" size={15} /> : <Send size={15} />}</button></div></form><div className="assistant-disclaimer"><ShieldCheck size={11} />原文是依据，请核查具体语境与实验条件</div>
            </aside>
          </section>
          <footer className="page-footer"><span>ScholarTrace · 研迹</span><p>让研究有迹可循</p><button onClick={() => setModal('roadmap')}>查看当前能力与开发进度 <ArrowRight size={12} /></button></footer>
        </div>
      </main>
      {notice && <div className={`toast ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.kind === 'success' ? <Check size={17} /> : notice.kind === 'error' ? <AlertTriangle size={17} /> : <Layers3 size={17} />}<span>{notice.text}</span><button onClick={() => setNotice(null)} aria-label="关闭提示"><X size={15} /></button></div>}
      {(modal || deleteDoc || renameDoc || reprocessDoc) && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !mutating) { setModal(null); setDeleteDoc(null); setRenameDoc(null); setReprocessDoc(null); } }}><div className={`modal ${deleteDoc || renameDoc ? 'small-modal' : ''}`} role="dialog" aria-modal="true" aria-labelledby="modal-title"><button className="modal-close icon-button" disabled={mutating} onClick={() => { setModal(null); setDeleteDoc(null); setRenameDoc(null); setReprocessDoc(null); }} aria-label="关闭弹窗"><X size={19} /></button>
        {reprocessDoc ? <><span className="modal-symbol"><RefreshCw size={23} /></span><div className="eyebrow">DOCUMENT PROCESSING</div><h2 id="modal-title">重新解析文献</h2><p>为“{reprocessDoc.title}”创建新的解析记录，原始 PDF 和历史证据继续保留。</p><div className="reprocess-options"><label><input type="checkbox" checked={reprocessOcr} disabled={!ocrReady || mutating} onChange={(event) => setReprocessOcr(event.target.checked)} /><span><strong>识别扫描文字</strong><small>{ocrReady ? '本地 OCR 模型已就绪，仅处理缺少文字层的页面。' : ocrSupported ? 'OCR 引擎可用，模型尚未准备完成。' : '当前环境尚未安装 OCR 引擎。'}</small></span></label><label><input type="checkbox" checked={reprocessTables} disabled={health?.capabilities.structured_tables !== true || mutating} onChange={(event) => setReprocessTables(event.target.checked)} /><span><strong>提取结构化表格</strong><small>{health?.capabilities.structured_tables === true ? '提取可识别的行列结构，结果仍需对照原表核验。' : '当前后端尚未提供结构化表格提取。'}</small></span></label></div><div className="modal-note"><ShieldCheck size={16} />OCR 可能误识别数字、公式和上下标，请核对原图。</div><div className="modal-actions"><button className="secondary-button" disabled={mutating} onClick={() => setReprocessDoc(null)}>取消</button><button className="primary-button" disabled={mutating || (reprocessOcr && !ocrReady)} onClick={() => void reprocessDocument()}>{mutating ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}开始解析</button></div></> : deleteDoc ? <><span className="modal-symbol danger"><Trash2 size={23} /></span><h2 id="modal-title">删除这篇文献？</h2><p>“{deleteDoc.title}”的文件、解析内容与检索记录将从当前工作区移除。</p><div className="modal-actions"><button className="secondary-button" disabled={mutating} onClick={() => setDeleteDoc(null)}>保留文献</button><button className="danger-button" disabled={mutating} onClick={() => void removeDocument()}>{mutating && <LoaderCircle className="spin" size={15} />}确认删除</button></div></> : renameDoc ? <><span className="modal-symbol"><PencilLine size={23} /></span><h2 id="modal-title">编辑文献标题</h2><p>调整文献库中的显示标题，原始 PDF 保持不变。</p><label className="modal-input-label">文献标题<input autoFocus value={renameTitle} onChange={(event) => setRenameTitle(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void renameDocument(); }} maxLength={500} /></label><div className="modal-actions"><button className="secondary-button" onClick={() => setRenameDoc(null)}>取消</button><button className="primary-button" disabled={mutating || !renameTitle.trim()} onClick={() => void renameDocument()}>{mutating && <LoaderCircle className="spin" size={15} />}保存标题</button></div></> : modal === 'roadmap' ? <><span className="modal-symbol"><GitBranch size={24} /></span><div className="eyebrow">BUILDING SCHOLARTRACE</div><h2 id="modal-title">从文献，走向完整研究闭环。</h2><p>当前开发文献发现、身份核对、arXiv 全文入库与主题追踪。模型功能仍需配置服务后验证。</p><div className="roadmap-list"><div className="done"><span><Check size={15} /></span><div><strong>01 · 文献与证据底座 <small>已完成</small></strong><p>PDF 批量上传、原生文本解析、内容块坐标、目录阅读、原文证据检索。</p></div></div><div className="done"><span>02</span><div><strong>扫描文字与结构化解析 <small>已接入</small></strong><p>本地 OCR、原文坐标与识别质量、表格结构、解析记录与历史证据。复杂公式、混合检索和多文核验继续完善。</p></div></div><div className="done"><span>03</span><div><strong>多文对比与联读 <small>已接入</small></strong><p>逐篇逐维证据矩阵、正文数字引用导航、可恢复翻译任务与双语对照。真实模型质量待验证。</p></div></div><div className="current"><span>04</span><div><strong>发现与追踪 <small>当前阶段</small></strong><p>arXiv/Crossref 检索、标识符核对、全文入库、关键词与赞踩排序、持久化主题检查。引用图谱、写作与 CSV Copilot 待接入。</p></div></div></div><div className="modal-note"><Clock3 size={16} />建设中的入口会在功能完成并验证后开放。</div></> : modal === 'settings' ? <><span className="modal-symbol"><Settings2 size={23} /></span><div className="eyebrow">CONNECTION & CAPABILITIES</div><h2 id="modal-title">连接与能力状态</h2><p>模型密钥仅由后端环境变量读取，不会通过浏览器保存。</p><div className="connection-row"><span>本地 API 服务</span><strong className={connectionError ? 'text-danger' : 'text-success'}>{connectionError ? '未连接' : '已连接'}</strong></div><div className="connection-row"><span>生成模型</span><strong>{health?.model_configured ? '已配置' : '未配置 · 使用证据检索'}</strong></div><div className="connection-row"><span>扫描文字识别</span><strong>{ocrReady ? '模型已就绪 · 可按需识别' : ocrSupported ? '引擎可用 · 模型待准备' : '尚未安装引擎'}</strong></div><div className="connection-row"><span>新上传自动 OCR</span><strong>{health?.capabilities.ocr_enabled === true ? '已开启' : '关闭 · 可手动重新解析'}</strong></div><div className="connection-row"><span>API 版本</span><strong>{health?.version || '等待连接'}</strong></div><div className="config-instruction"><strong>启用模型回答</strong><p>在项目根目录的 <code>.env</code> 中配置 <code>LLM_BASE_URL</code>、<code>LLM_API_KEY</code>、<code>LLM_MODEL</code>，然后重启后端。具体要求见 README。</p></div>{health?.capabilities && <div className="capability-list">{Object.entries(health.capabilities).map(([key, value]) => <span key={key} className={value === true ? 'enabled' : ''}>{value === true ? <Check size={12} /> : <Clock3 size={12} />}{({ pdf_upload: 'PDF 上传', native_pdf_parsing: '原生文字解析', evidence_search: '证据检索', ocr: '扫描件 OCR', ocr_supported: 'OCR 引擎', ocr_enabled: '自动 OCR', ocr_ready: 'OCR 模型就绪', structured_tables: '结构化表格', llm: '模型回答', translation: '翻译接口', comparison_matrix: '多文对比', citation_navigation: '正文引用导航', literature_search: '文献检索', arxiv_import: 'arXiv 全文入库', topic_tracking: '主题追踪', citation_graph: '引用图谱' } as Record<string, string>)[key] || key}</span>)}</div>}<button className="secondary-button" onClick={() => void refresh()}><RefreshCw size={15} />刷新连接状态</button></> : <><span className="modal-symbol"><BookOpen size={24} /></span><div className="eyebrow">A SMALL GUIDE</div><h2 id="modal-title">从一篇 PDF 开始。</h2><p>这是一套真实连接本地后端的研究工作台，文献会保存在你的本地数据目录中。</p><ol className="help-list"><li><strong>导入与等待解析</strong><p>点击“导入文献”或拖入 PDF，进度按实际处理页数更新。扫描页会显示识别状态；模型就绪后可通过文献操作菜单重新解析。</p></li><li><strong>沿着结构阅读</strong><p>选择文献，在“目录”中跳转章节，或用页码和“结构文本”查看解析内容。</p></li><li><strong>提问并检查原文</strong><p>右侧输入问题，点击证据卡片定位原文高亮。勾选多篇文献可联合检索。</p></li><li><strong>理解当前能力边界</strong><p>未配置模型时只检索原文；有出处也需要检查语境。OCR 文字与表格仍需核对原图；复杂公式、图谱和写作功能继续按阶段接入。</p></li></ol><button className="primary-button" onClick={() => { setModal(null); inputRef.current?.click(); }}><Plus size={16} />导入文献</button></>}
      </div></div>}
      {literatureOpen && <Suspense fallback={<div className="modal-backdrop"><div className="modal"><LoaderCircle className="spin" />正在加载文献发现…</div></div>}><LiteraturePanel initialQuery={literatureQuery} onClose={() => setLiteratureOpen(false)} onImported={document => { void refresh(); setSelectedId(document.id); }} /></Suspense>}
      {comparisonOpen && <Suspense fallback={<div className="modal-backdrop"><div className="modal"><LoaderCircle className="spin" />正在加载多文对比…</div></div>}><ComparisonPanel documents={documents} initialIds={checkedIds.length >= 2 ? checkedIds : availableDocuments.slice(0, 2).map(item => item.id)} onClose={() => setComparisonOpen(false)} onSource={jumpToEvidence} /></Suspense>}
    </div>
  );
}
