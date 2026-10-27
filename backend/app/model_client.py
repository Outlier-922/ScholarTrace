"""Small structured model adapter; credentials stay in the server environment."""
from __future__ import annotations

import hashlib
import json
import os
import re
from urllib.parse import urlsplit

import httpx


def configured() -> bool:
    return all(os.getenv(key, "").strip() for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"))


def identity() -> str:
    value = os.getenv("LLM_BASE_URL", "").strip() + "|" + os.getenv("LLM_MODEL", "").strip()
    return hashlib.sha256(value.encode()).hexdigest()[:24]


async def chat_json(system: str, payload: dict, *, max_tokens: int = 2000) -> dict:
    if not configured():
        raise ValueError("model_not_configured")
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
    if parsed.hostname == "api.deepseek.com":
        body["thinking"] = {"type": "disabled"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
        response = await client.post(url, headers={"Authorization": "Bearer " + os.getenv("LLM_API_KEY", "").strip()}, json=body)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise ValueError("incomplete_model_output")
        raw = choice["message"]["content"]
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValueError("invalid_model_output")
    result = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    if not isinstance(result, dict):
        raise ValueError("invalid_model_output")
    return result
