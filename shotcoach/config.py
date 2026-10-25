"""Configuration: .env discovery, model client, and pricing.

The app looks for a .env file in the project folder first and then one folder
above it, so it works both for a local checkout and for graders who keep a
shared .env next to their submissions folder.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
LOGS_DIR = PROJECT_ROOT / "logs"

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_PORTKEY_BASE_URL = "https://api.portkey.ai/v1"

# USD per 1M tokens for gpt-6-luna (third-party listings, Oct 2026). Override
# with PRICE_INPUT_PER_M / PRICE_OUTPUT_PER_M if the official price differs.
PRICE_INPUT_PER_M = float(os.getenv("PRICE_INPUT_PER_M", "0.10"))
PRICE_CACHED_INPUT_PER_M = float(os.getenv("PRICE_CACHED_INPUT_PER_M", "0.01"))
PRICE_OUTPUT_PER_M = float(os.getenv("PRICE_OUTPUT_PER_M", "0.50"))

# Hard cap on tool-calling rounds for the Settings Engineer. Stops a confused
# model from looping and running up cost.
MAX_TOOL_ROUNDS = 6


def env_file_candidates() -> list[Path]:
    """The two places a .env may live: the project folder, then its parent."""

    return [PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"]


def load_env() -> Path | None:
    """Load the first .env found. Real environment variables always win."""

    for env_file in env_file_candidates():
        if env_file.is_file():
            load_dotenv(env_file, override=False)
            return env_file
    return None


def model_name() -> str:
    return os.getenv("SHOTCOACH_MODEL", DEFAULT_MODEL)


@lru_cache(maxsize=1)
def get_client():
    """OpenAI client routed through Portkey (course key) or OpenAI directly."""

    from openai import OpenAI

    load_env()
    portkey_key = os.getenv("PORTKEY_API_KEY")
    if portkey_key:
        return OpenAI(
            api_key=portkey_key,
            base_url=os.getenv("PORTKEY_BASE_URL", DEFAULT_PORTKEY_BASE_URL),
            default_headers={"x-portkey-provider": os.getenv("PORTKEY_PROVIDER", "openai")},
            timeout=90,
        )
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        return OpenAI(api_key=openai_key, timeout=90)
    raise RuntimeError(
        "No API key found. Put PORTKEY_API_KEY (or OPENAI_API_KEY) in a .env file "
        "in the project folder or one folder above it."
    )


def cost_usd(input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    uncached = max(input_tokens - cached_tokens, 0)
    return (
        uncached * PRICE_INPUT_PER_M
        + cached_tokens * PRICE_CACHED_INPUT_PER_M
        + output_tokens * PRICE_OUTPUT_PER_M
    ) / 1_000_000
