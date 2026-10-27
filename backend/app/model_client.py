"""Small structured model adapter; credentials stay in the server environment."""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

_budget=ContextVar('scholartrace_model_budget',default=None)


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
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
        response = await client.post(url, headers={"Authorization": "Bearer " + os.getenv("LLM_API_KEY", "").strip()}, json=body)
        response.raise_for_status()
        output=response.json()
        if budget is not None:budget.record(output.get('usage'))
        choice = output["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise ValueError("incomplete_model_output")
        raw = choice["message"]["content"]
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValueError("invalid_model_output")
    result = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    if not isinstance(result, dict):
        raise ValueError("invalid_model_output")
    return result
