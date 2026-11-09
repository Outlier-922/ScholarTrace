import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import type { SourceLocation } from './types';

interface Version {
  id: string;
  filename: string;
  version_label: string | null;
  page_count: number;
  status: string;
}
interface Versions {
  items: Version[];
  latest_readable_version: string | null;
  latest_readable_ids: string[];
  preferred_document_id: string | null;
  existing_document_id: string | null;
}

export default function WorkVersions({ documentId, groupRevision, onSelect, onSource, readOnly, onDefaultChanged }: {
  documentId: string;
  groupRevision: string;
  onSelect: (id: string) => void;
  onSource: (location: SourceLocation) => void;
  readOnly: boolean;
  onDefaultChanged: () => void;
}) {
  const [versions, setVersions] = useState<Versions | null>(null);
  const [error, setError] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [choiceMessage, setChoiceMessage] = useState('');
  const [choiceError, setChoiceError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    void request<Versions>(`/documents/${documentId}/work-versions`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setVersions(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [documentId, groupRevision, reload]);

  async function chooseDefault(action: 'keep' | 'replace') {
    if (saving || readOnly || !versions?.existing_document_id) return;
    setSaving(true);
    setChoiceMessage('');
    setChoiceError('');
    try {
      const next = await request<{ preferred_document_id: string }>(`/documents/${documentId}/work-default`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, expected_document_id: versions.existing_document_id }),
      });
      setVersions(current => current && {
        ...current,
        preferred_document_id: next.preferred_document_id,
        existing_document_id: next.preferred_document_id,
      });
      setChoiceMessage(action === 'keep' ? '已保留现有默认版本' : '已覆盖默认版本');
      onDefaultChanged();
    } catch (cause) {
      setChoiceError(cause instanceof Error ? cause.message : '保存默认版本失败');
      setReload(current => current + 1);
    } finally {
      setSaving(false);
    }
  }

  if (error) return <p className="work-version-picker" role="status">暂时无法读取作品版本</p>;
  if (!versions || versions.items.length < 2) return null;
  const readable = versions.items.filter(item => item.status === 'ready' || item.status === 'partial');
  const baseline = readable.find(item => item.id === documentId);
  const peers = readable.filter(item => item.id !== documentId);
  const preferred = readable.find(item => item.id === versions.preferred_document_id);
  const existing = readable.find(item => item.id === versions.existing_document_id);
  return <div className="work-version-picker">
    <strong>检测到同一作品的多个文件</strong>
    <label>作品版本 <select aria-label="切换作品版本" value={documentId} onChange={event => onSelect(event.target.value)}>
      {versions.items.map(item => <option key={item.id} value={item.id}>
        {versionName(item)} · {item.page_count} 页{item.id === preferred?.id ? ' · 默认' : ''}
      </option>)}
    </select></label>
    <span>{versions.latest_readable_version
      ? `同 arXiv 标识的库内最新可读版本 ${versions.latest_readable_version}${versions.latest_readable_ids.length > 1 ? '（有多个文件）' : ''}`
      : '版本顺序待核对'}</span>
    {preferred && <span>问答默认版本 · {versionName(preferred)}</span>}
    {baseline && peers.length > 0 && <>
      <button className="secondary-button" onClick={() => setComparing(true)}>对照版本文本</button>
      {comparing && <VersionComparison baseline={baseline} peers={peers}
        initialId={peers.find(peer => versions.latest_readable_ids.includes(peer.id))?.id ?? peers[0].id}
        onClose={() => setComparing(false)} onSource={location => {
          setComparing(false);
          onSource(location);
        }} />}
    </>}
    {!readOnly && baseline && peers.length > 0 && existing && <details className="work-version-default">
      <summary>保留或覆盖版本</summary>
      <p>现有版本 · {versionName(existing)}</p>
      <p>当前文件 · {versionName(baseline)}</p>
      <p>选择将用于整个文献库问答。各版本仍可单独阅读、对照和引用。</p>
      <div>
        <button className="secondary-button" disabled={saving} onClick={() => void chooseDefault('keep')}>保留现有默认版本</button>
        <button className="secondary-button" disabled={saving || existing.id === documentId}
          onClick={() => void chooseDefault('replace')}>以当前文件覆盖默认版本</button>
      </div>
      {choiceError && <p role="alert">{choiceError}</p>}
      {choiceMessage && <p role="status">{choiceMessage}</p>}
    </details>}
  </div>;
}

function versionName(item: Version) {
  return `${item.version_label || '版本未注明'} · ${item.filename}`;
}

interface Excerpt extends SourceLocation {
  excerpt: string;
  last_page: number;
  block_count: number;
}

interface Comparison {
  partial_parse: boolean;
  total: number;
  previous_offset: number | null;
  next_offset: number | null;
  items: { kind: 'insert' | 'delete' | 'replace'; left: Excerpt | null; right: Excerpt | null }[];
}

const changeNames = { insert: '仅对照侧有文本', delete: '仅基准侧有文本', replace: '两侧文本不同' };

function VersionComparison({ baseline, peers, initialId, onClose, onSource }: {
  baseline: Version;
  peers: Version[];
  initialId: string;
  onClose: () => void;
  onSource: (location: SourceLocation) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [otherId, setOtherId] = useState(initialId);
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<Comparison | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({ other_id: otherId, offset: String(offset) });
    void request<Comparison>(`/documents/${baseline.id}/version-diff?${query}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setResult(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [baseline.id, otherId, offset]);

  function changePage(nextOffset: number) {
    setResult(null);
    setError('');
    setOffset(nextOffset);
    dialogRef.current?.scrollTo(0, 0);
  }

  return <dialog className="version-comparison" ref={dialogRef} aria-labelledby="version-comparison-title"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><h2 id="version-comparison-title">版本文本对照</h2>
      <button className="secondary-button" onClick={onClose}>关闭</button>
    </header>
    <div className="version-comparison-pair">
      <div>基准版本 <strong>{versionName(baseline)}</strong></div>
      <label>对照版本 <select value={otherId} onChange={event => {
        setOtherId(event.target.value);
        changePage(0);
      }}>{peers.map(peer => <option key={peer.id} value={peer.id}>{versionName(peer)}</option>)}</select></label>
    </div>
    <p>按解析顺序比对标题和段落，忽略空白变化。分页、分段和识别误差也可能产生差异，请对照 PDF 原文。</p>
    {error ? <p role="alert">{error}</p> : !result ? <p role="status">正在对照版本文本…</p> : <>
      {result.partial_parse && <p role="status">至少一侧只完成了部分解析，结果仅覆盖已提取的文本。</p>}
      <p role="status">{result.total ? `第 ${offset + 1}–${offset + result.items.length} 组，共 ${result.total} 组差异` : '已比对的文本块一致。'}</p>
      {result.items.map((change, index) => <article key={offset + index}>
        <h3>{offset + index + 1} · {changeNames[change.kind]}</h3>
        <div className="version-comparison-pair">
          <DiffSide name="基准版本" item={change.left} onSource={onSource} />
          <DiffSide name="对照版本" item={change.right} onSource={onSource} />
        </div>
      </article>)}
      {(result.previous_offset !== null || result.next_offset !== null) && <footer>
        <button className="secondary-button" disabled={result.previous_offset === null}
          onClick={() => changePage(result.previous_offset!)}>上一组列表</button>
        <button className="secondary-button" disabled={result.next_offset === null}
          onClick={() => changePage(result.next_offset!)}>下一组列表</button>
      </footer>}
    </>}
  </dialog>;
}

function DiffSide({ name, item, onSource }: {
  name: string;
  item: Excerpt | null;
  onSource: (location: SourceLocation) => void;
}) {
  return <section>
    <h4>{name}{item && ` · 第 ${item.page}${item.last_page === item.page ? '' : `–${item.last_page}`} 页 · ${item.block_count} 块`}</h4>
    {item ? <>
      <p>{item.excerpt}</p>
      <button className="secondary-button" onClick={() => onSource(item)}
        aria-label={`查看${name}第 ${item.page} 页原文`}>定位首块原文</button>
    </> : <p>无对应文本块</p>}
  </section>;
}
