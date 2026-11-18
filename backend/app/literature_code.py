"""Check only public GitHub repositories declared in registry metadata."""

import json
import re

import httpx

from . import literature_store as repo, store

_URL = re.compile(r"https://github\.com/([a-z0-9-]{1,39})/([a-z0-9_.-]{1,100})", re.I)
_OPEN_LICENSES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0",
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-2.1", "LGPL-2.1-only", "LGPL-3.0", "LGPL-3.0-only", "AGPL-3.0", "AGPL-3.0-only",
    "Unlicense",
}


def repository(url):
    match = _URL.fullmatch(url or "")
    if not match or match[2] in {".", ".."}:
        return None
    return (match[1] + "/" + match[2]).casefold()


def cached(url):
    key = repository(url)
    return repo.cache_get("github-repo-v1:" + key) if key else None


async def _fetch(api_url):
    async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=6), follow_redirects=False) as client:
        async with client.stream("GET", api_url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ScholarTrace public repository verification",
        }) as response:
            if response.status_code == 404:
                return "not_found", None
            if response.status_code == 429 or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
            ):
                return "rate_limited", None
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 256_000:
                    raise ValueError("github_metadata_too_large")
            return "public", json.loads(body)


async def verify(url):
    key = repository(url)
    if key is None:
        return None
    if previous := cached(url):
        return previous
    api_url = "https://api.github.com/repos/" + key
    result = {"status": "unverified", "checked_at": store.now(), "source_url": api_url}
    try:
        status, payload = await _fetch(api_url)
        if payload is None:
            result["status"] = status
        elif payload.get("full_name", "").casefold() == key and payload.get("private") is False:
            license_metadata = payload.get("license") or {}
            spdx = license_metadata.get("spdx_id")
            result.update(status="open_license_reported" if spdx in _OPEN_LICENSES else "public_license_unconfirmed",
                          license_id=spdx if isinstance(spdx, str) and re.fullmatch(r"[a-zA-Z0-9.-]{1,60}", spdx) else None)
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        pass
    # An ordinary network or protocol error can be retried without hiding a previously declared link.
    if result["status"] != "unverified":
        repo.cache_put("github-repo-v1:" + key, result)
    return result
