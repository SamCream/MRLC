from __future__ import annotations

import os

STRATA_URL_ENV = "MRLC_STRATA_URL"
CCR_BASE_URL_ENV = "MRLC_CCR_BASE_URL"
STRATA_API_KEY_ENV = "MRLC_STRATA_API_KEY"
DEFAULT_STRATA_URL = "http://127.0.0.1:18203"
DEFAULT_CCR_BASE_URL = "http://127.0.0.1:3456"


def _resolve(env_name: str, default: str) -> str:
    value = (os.environ.get(env_name) or "").strip()
    return (value or default).rstrip("/")


def strata_url() -> str:
    return _resolve(STRATA_URL_ENV, DEFAULT_STRATA_URL)


def ccr_base_url() -> str:
    return _resolve(CCR_BASE_URL_ENV, DEFAULT_CCR_BASE_URL)
