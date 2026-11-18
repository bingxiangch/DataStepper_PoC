"""Runtime configuration for the Data Agent Debugger POC."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("OLIST_DATA_DIR", ROOT / "data" / "olist")).resolve()
RUNS_DIR = Path(os.getenv("RUNS_DIR", ROOT / "runs")).resolve()
FRONTEND_DIST = ROOT / "frontend" / "dist"

RUNS_DIR.mkdir(parents=True, exist_ok=True)
(RUNS_DIR / "artifacts").mkdir(parents=True, exist_ok=True)


def _provider() -> str | None:
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in {"anthropic", "openai"}:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


LLM_PROVIDER = _provider()
LLM_MODEL = os.getenv("LLM_MODEL") or (
    "claude-sonnet-5-5" if LLM_PROVIDER == "anthropic" else "gpt-4o-mini"
)
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LIVE_AVAILABLE = bool(LLM_PROVIDER and (
    os.getenv("ANTHROPIC_API_KEY") if LLM_PROVIDER == "anthropic" else os.getenv("OPENAI_API_KEY")
))


def live_status() -> dict:
    """Truthful availability metadata; never exposes a key."""
    if LIVE_AVAILABLE:
        return {
            "available": True,
            "provider": LLM_PROVIDER,
            "model": LLM_MODEL,
            "reason": None,
        }
    return {
        "available": False,
        "provider": LLM_PROVIDER,
        "model": None,
        "reason": "No LLM API key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env.",
    }
