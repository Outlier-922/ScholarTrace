const API_ROOT = '/api';

async function open(path: string, init?: RequestInit) {
  const response = await fetch(`${API_ROOT}${path}`, init);
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      detail = typeof body.detail === 'string' ? body.detail : body.message || detail;
    } catch { /* HTTP status remains useful when no JSON body is available. */ }
    throw new Error(detail);
  }
  return response;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await open(path, init);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const postJSON = <T>(path: string, body: unknown, signal?: AbortSignal) => request<T>(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
  signal,
});

export type AnswerProgress = { type:'progress'; phase:'retrieving'|'generating'|'validating'; fragments?:number }
  | { type:'answer_delta'; text:string };

export async function streamAnswer<T>(body:unknown, onEvent:(event:AnswerProgress)=>void, signal:AbortSignal):Promise<T> {
  const response = await open('/ask/stream', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal,
  });
  const reader = response.body?.getReader();
  if (!reader) throw new Error('浏览器未能打开问答流。');
  const decoder = new TextDecoder();
  let pending = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error('问答流已中断，请重试。');
      pending += decoder.decode(value, { stream:true });
      const lines = pending.split('\n');
      pending = lines.pop() ?? '';
      for (const line of lines) {
        if (!line) continue;
        const event = JSON.parse(line) as AnswerProgress | {type:'result';result:T} | {type:'error';detail:string};
        if (event.type === 'result') return event.result;
        if (event.type === 'error') throw new Error(event.detail);
        onEvent(event);
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
