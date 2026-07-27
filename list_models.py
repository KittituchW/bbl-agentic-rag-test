"""List the model IDs the configured provider actually serves.

Model names in a provider's console are display names ("Gemini 3.5 Flash
Lite"); the API wants an ID ("gemini-3.5-flash-lite"). This prints the real
IDs so a wrong guess fails here rather than halfway through a sample run.

    python list_models.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from openai import AsyncOpenAI

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


async def run() -> int:
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "google":
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            print("error: GOOGLE_API_KEY is not set", file=sys.stderr)
            return 2
        client = AsyncOpenAI(base_url=os.getenv("GOOGLE_BASE_URL", GOOGLE_BASE_URL), api_key=key)
        configured = os.getenv("GOOGLE_MODEL", "gemini-3.5-flash-lite")
    elif provider == "bbl":
        client = AsyncOpenAI(
            base_url=os.getenv("BBL_BASE_URL", "https://apimsdbxcandidate01.azure-api.net/llm"),
            api_key="unused",
            default_headers={"api-key": os.environ["BBL_API_KEY"]},
        )
        configured = os.getenv("BBL_MODEL", "gpt-5-mini")
    else:
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        configured = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    print(f"provider={provider}\nconfigured model: {configured}\n")
    try:
        listing = await client.models.list()
    except Exception as exc:
        print(f"could not list models: {type(exc).__name__}: {exc}", file=sys.stderr)
        await client.close()
        return 1

    ids = sorted(m.id for m in listing.data)
    for model_id in ids:
        print(f"  {model_id}{'  <- configured' if model_id.endswith(configured) else ''}")
    if not any(m.endswith(configured) for m in ids):
        print(f"\nWARNING: {configured!r} not in this list — update .env", file=sys.stderr)
    print(f"\n{len(ids)} model(s)")
    await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
