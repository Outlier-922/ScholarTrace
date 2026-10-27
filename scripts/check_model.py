"""Check the configured provider with a tiny request and no document transmission."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)
from backend.app import model_client


async def main():
    if not model_client.configured():
        print("模型未配置：请在 .env 填写 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。")
        return 2
    try:
        result = await model_client.chat_json('Output exactly one JSON object with status set to ok. This is a connection check.', {}, max_tokens=100)
        if result.get("status") != "ok":
            raise ValueError("unexpected_response")
    except Exception as error:
        # Provider error bodies can echo credentials; do not print raw exceptions.
        print(f"模型检查失败（{type(error).__name__}）。请核对密钥、模型名、可用地区及额度。密钥未输出。")
        return 1
    print("模型 JSON 接口连接成功。仅发送了固定检查提示，没有发送论文内容。")
    return 0


if __name__ == "__main__":
    # Fail before creating Windows' socket-based event loop in a restricted shell.
    if not model_client.configured():
        print("模型未配置：请在 .env 填写 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main()))
