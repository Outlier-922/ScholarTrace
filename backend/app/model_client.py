"""Small structured model adapter; credentials stay in the server environment."""
from __future__ import annotations

import hashlib
import asyncio
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

_budget=ContextVar('scholartrace_model_budget',default=None)
_stream_observer = ContextVar('scholartrace_stream_observer', default=None)


@dataclass
class CallBudget:
    max_calls:int
    max_output_tokens:int
    calls:int=0
    reserved_output_tokens:int=0
    input_tokens:int=0
    output_tokens:int=0
    unreported_usage:int=0
    exhausted:bool=False
    observer:object=None

    def reserve(self,tokens):
        if self.calls>=self.max_calls or self.reserved_output_tokens+tokens>self.max_output_tokens:
            self.exhausted=True
            raise ValueError('model_budget_exhausted')
        self.calls+=1;self.reserved_output_tokens+=tokens;self.unreported_usage+=1
        if self.observer:self.observer(self.snapshot())

    def record(self,usage):
        if isinstance(usage,dict) and all(isinstance(usage.get(key),int) and not isinstance(usage[key],bool) and 0<=usage[key]<=10_000_000 for key in ['prompt_tokens','completion_tokens']):
            self.input_tokens+=usage['prompt_tokens'];self.output_tokens+=usage['completion_tokens']
            self.unreported_usage=max(0,self.unreported_usage-1)
        if self.observer:self.observer(self.snapshot())

    def snapshot(self):
        return {key:getattr(self,key) for key in ['calls','reserved_output_tokens','input_tokens','output_tokens','unreported_usage','exhausted']}


@contextmanager
def metered(budget):
    token=_budget.set(budget)
    try:yield budget
    finally:_budget.reset(token)


@contextmanager
def stream_progress(observer):
    token = _stream_observer.set(observer)
    try:
        yield
    finally:
        _stream_observer.reset(token)


def report_progress(phase, **fields):
    if observer := _stream_observer.get():
        observer({'type':'progress', 'phase':phase, **fields})


def configuration():
    missing=[key for key in ['LLM_BASE_URL','LLM_API_KEY','LLM_MODEL'] if not os.getenv(key,'').strip()]
    endpoint=os.getenv('LLM_BASE_URL','').strip().rstrip('/')
    try:
        parsed=urlsplit(endpoint)
        valid=(parsed.scheme=='https' or parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'}) and bool(parsed.hostname) and not any([parsed.username,parsed.password,parsed.query,parsed.fragment])
        if parsed.port is not None and not 1<=parsed.port<=65535:valid=False
    except ValueError:
        parsed=None;valid=False
    profile=os.getenv('LLM_API_PROFILE','auto').strip() or 'auto'
    if profile=='auto':profile='openai' if parsed and parsed.hostname=='api.openai.com' else 'compatible'
    effort=os.getenv('LLM_REASONING_EFFORT','').strip()
    error='missing_configuration' if missing else 'invalid_model_endpoint' if not valid else 'invalid_api_profile' if profile not in {'openai','compatible'} else 'invalid_reasoning_effort' if effort and effort not in {'none','minimal','low','medium','high','xhigh','max'} else None
    return {'configured':not missing,'ready':error is None,'missing_fields':missing,'error':error,
            'profile':profile if profile in {'openai','compatible'} else 'invalid','endpoint_host':parsed.hostname if valid else None,
            'model':os.getenv('LLM_MODEL','').strip()[:150] or None,'reasoning_effort':effort or None}


def configured() -> bool:
    return all(os.getenv(key, "").strip() for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"))


def identity() -> str:
    value = '|'.join(os.getenv(key,'').strip() for key in ['LLM_BASE_URL','LLM_MODEL','LLM_API_PROFILE','LLM_REASONING_EFFORT'])
    return hashlib.sha256(value.encode()).hexdigest()[:24]


async def chat_json(system: str, payload: dict, *, max_tokens: int = 2000) -> dict:
    if not configured():
        raise ValueError("model_not_configured")
    config=configuration()
    if not config['ready']:raise ValueError(config['error'])
    endpoint = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("invalid_model_endpoint")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid_model_endpoint")
    url = endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"
    body = {
            "model": os.getenv("LLM_MODEL", "").strip(), "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        }
    if config['profile']=='openai':
        body.pop('temperature')
        body['max_completion_tokens']=body.pop('max_tokens')
    if config['reasoning_effort']:body['reasoning_effort']=config['reasoning_effort']
    if parsed.hostname == "api.deepseek.com":
        body["thinking"] = {"type": "disabled"}
    if (budget:=_budget.get()) is not None:budget.reserve(max_tokens)
    from .demo_access import reserve_model
    reserve_model(max_tokens)
    headers = {"Authorization": "Bearer " + os.getenv("LLM_API_KEY", "").strip()}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
        if _stream_observer.get() is not None:
            async with asyncio.timeout(85):
                raw = await _read_stream(client, url, headers, body, budget)
        else:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            raw = _raw_choice(response.json(), budget)
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValueError("invalid_model_output")
    result = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    if not isinstance(result, dict):
        raise ValueError("invalid_model_output")
    return result


def _raw_choice(output, budget):
    if budget is not None:
        budget.record(output.get('usage'))
    choice = output["choices"][0]
    if choice.get("finish_reason") in {"length", "content_filter"}:
        raise ValueError("incomplete_model_output")
    return choice["message"]["content"]


async def _read_stream(client, url, headers, body, budget):
    report_progress('generating', fragments=0)
    async with client.stream('POST', url, headers=headers, json={**body, 'stream':True}) as response:
        response.raise_for_status()
        if 'text/event-stream' not in response.headers.get('content-type', ''):
            await response.aread()
            return _raw_choice(response.json(), budget)
        parts = []
        size = 0
        finished = False
        async for line in response.aiter_lines():
            if not line.startswith('data:'):
                continue
            wire = line[5:].strip()
            if wire == '[DONE]':
                break
            if len(wire) > 120000:
                raise ValueError('model_stream_frame_limit')
            frame = json.loads(wire)
            if budget is not None and frame.get('usage'):
                budget.record(frame['usage'])
            for choice in frame.get('choices', []):
                if choice.get('index', 0) != 0:
                    continue
                finish = choice.get('finish_reason')
                if finish and finish != 'stop':
                    raise ValueError('incomplete_model_output')
                finished = finished or finish == 'stop'
                piece = (choice.get('delta') or {}).get('content')
                if isinstance(piece, str) and piece:
                    if size + len(piece) > 100000 or len(parts) >= 20000:
                        raise ValueError('invalid_model_output')
                    parts.append(piece)
                    size += len(piece)
                    if len(parts) == 1 or len(parts) % 12 == 0:
                        report_progress('generating', fragments=len(parts))
        if not finished:
            raise ValueError('incomplete_model_output')
    report_progress('generating', fragments=len(parts))
    return ''.join(parts)
