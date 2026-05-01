"""
Rate Limit Handler for Gemini and Gemma Models
===============================================
Handles per-model rate limiting with:
- Proactive delay before hitting limits
- Reactive retry on 429 errors
- Tracks requests/min, tokens/min, requests/day independently per model
- Tracks cumulative context window usage per conversation
- Persists daily request counts to a single JSON file (one entry per model)
  so daily quota survives app restarts and resets at calendar midnight
- Auto-detects model family (Gemini vs Gemma) and applies appropriate limits
"""

import os
import re
import json
import time
import logging
from collections import deque
from datetime import datetime
from typing import Callable, Any
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Custom exception for Gemini fallback trigger
class GeminiFallbackRequired(Exception):
    """Raised when Gemini model needs to fallback to Gemma."""
    pass

# Single shared file for all models
PERSISTENCE_FILE = "rate_limit_state.json"

# ── Rate Limits per Model Family ────────────────────────────────────────────────

# Gemini model rate limits (free tier)
GEMINI_RATE_LIMITS = {
    "requests_per_minute": 15,
    "tokens_per_minute":   250000,
    "requests_per_day":    500,
}

# Gemma 3 model rate limits (free tier)
GEMMA_3_RATE_LIMITS = {
    "requests_per_minute": 30,
    "tokens_per_minute":   15000,
    "requests_per_day":    14400,
}

# Gemma 4 model rate limits (free tier)
# RPM: 15, TPM: unlimited (large int as practical ceiling), RPD: 1500
GEMMA_4_RATE_LIMITS = {
    "requests_per_minute": 15,
    "tokens_per_minute":   10_000_000,  # effectively unlimited — TPM is not enforced
    "requests_per_day":    1500,
}


def get_rate_limits(model_name: str) -> dict:
    """
    Detect model family from model_name and return appropriate rate limits.

    Each generation is matched explicitly by version number so ordering
    doesn't matter and future model families won't silently inherit wrong limits.

    Args:
        model_name: Model identifier (e.g., "gemma-4-31b-it", "gemma-3-27b-it")

    Returns:
        dict with keys: requests_per_minute, tokens_per_minute, requests_per_day
    """
    if model_name.startswith("gemini-"):
        return GEMINI_RATE_LIMITS.copy()
    elif model_name.startswith("gemma-4-"):
        return GEMMA_4_RATE_LIMITS.copy()
    elif model_name.startswith("gemma-3-") or model_name.startswith("gemma-3n-"):
        return GEMMA_3_RATE_LIMITS.copy()
    else:
        # Unknown model — default to Gemma 3 limits and log a warning
        logger.warning(
            f"[rate_limit_handler] Unrecognised model '{model_name}' — "
            f"defaulting to Gemma 3 rate limits. Add explicit limits if needed."
        )
        return GEMMA_3_RATE_LIMITS.copy()


# ── Gemini Fallback Tracking (per session) ─────────────────────────────────────
_gemini_fallback_used = False
_gemini_fallback_agents = []


def _is_daily_limit_exhaustion(error_message: str) -> bool:
    error_str = str(error_message).upper()
    return "QUOTA" in error_str or "RESOURCE_EXHAUSTED" in error_str or "RATE_LIMIT" in error_str


def get_gemini_fallback_status() -> dict:
    return {
        "fallback_used": _gemini_fallback_used,
        "agents_affected": _gemini_fallback_agents.copy(),
    }


class RateLimitHandler:
    """
    Manages rate limiting for Gemini and Gemma models.

    Rate limits are auto-detected based on model name prefix:

        Gemini models (free tier):
            - 15 requests / minute
            - 250,000 tokens / minute
            - 500 requests / day

        Gemma 4 models (free tier):
            - 15 requests / minute
            - Unlimited tokens / minute (10M used as practical ceiling)
            - 1,500 requests / day

        Gemma 3 models (free tier):
            - 30 requests / minute
            - 15,000 tokens / minute
            - 14,400 requests / day

    Daily request count is persisted to a single shared JSON file.
    The counter resets automatically when the calendar date changes.
    """

    def __init__(
        self,
        model_name: str,
        requests_per_minute: int | None = None,
        tokens_per_minute: int | None = None,
        requests_per_day: int | None = None,
        max_retries: int = 5,
        max_retries_service_unavailable: int | None = None,
        base_backoff: float = 2.0,
        safety_margin: float = 0.8,
    ):
        self.model_name = model_name
        self.max_retries = max_retries
        self.max_retries_service_unavailable = max_retries_service_unavailable if max_retries_service_unavailable is not None else max_retries
        self.base_backoff = base_backoff
        self.on_wait: Callable | None = None

        if requests_per_minute is None or tokens_per_minute is None or requests_per_day is None:
            detected_limits = get_rate_limits(model_name)
            requests_per_minute = requests_per_minute or detected_limits["requests_per_minute"]
            tokens_per_minute   = tokens_per_minute   or detected_limits["tokens_per_minute"]
            requests_per_day    = requests_per_day    or detected_limits["requests_per_day"]

        self.rpm_limit = int(requests_per_minute * safety_margin)
        self.tpm_limit = int(tokens_per_minute   * safety_margin)
        self.rpd_limit = int(requests_per_day    * safety_margin)

        self._minute_requests: deque = deque()
        self._minute_tokens:   deque = deque()
        self._day_request_count: int = 0

        self._load_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _load_all(self) -> dict:
        if not os.path.exists(PERSISTENCE_FILE):
            return {}
        try:
            with open(PERSISTENCE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning(f"Could not read {PERSISTENCE_FILE}, starting fresh.")
            return {}

    def _save_all(self, data: dict):
        try:
            with open(PERSISTENCE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not save {PERSISTENCE_FILE}: {e}")

    def _load_state(self):
        data  = self._load_all()
        entry = data.get(self.model_name, {})
        stored_date  = entry.get("date", "")
        stored_count = entry.get("day_request_count", 0)

        if stored_date == self._today():
            self._day_request_count = stored_count
            logger.info(
                f"[{self.model_name}] Resumed: {self._day_request_count} requests "
                f"used today ({stored_date})."
            )
        else:
            self._day_request_count = 0

    def _save_state(self):
        data = self._load_all()
        data[self.model_name] = {
            "date":              self._today(),
            "day_request_count": self._day_request_count,
            "saved_at":          self._now_str(),
            "minute_requests":   f"{self._current_minute_requests()}/{self.rpm_limit}",
            "minute_tokens":     f"{self._current_minute_tokens()}/{self.tpm_limit}",
            "day_requests":      f"{self._day_request_count}/{self.rpd_limit}",
        }
        self._save_all(data)

    # ------------------------------------------------------------------
    # Sliding window helpers
    # ------------------------------------------------------------------

    def _cleanup_windows(self):
        now        = time.time()
        minute_ago = now - 60

        while self._minute_requests and self._minute_requests[0] < minute_ago:
            self._minute_requests.popleft()

        while self._minute_tokens and self._minute_tokens[0][0] < minute_ago:
            self._minute_tokens.popleft()

    def _current_minute_requests(self) -> int:
        self._cleanup_windows()
        return len(self._minute_requests)

    def _current_minute_tokens(self) -> int:
        self._cleanup_windows()
        return sum(t for _, t in self._minute_tokens)

    # ------------------------------------------------------------------
    # Proactive delay
    # ------------------------------------------------------------------

    def _wait_if_needed(self, estimated_tokens: int):
        while True:
            self._cleanup_windows()
            now       = time.time()
            wait_time = 0.0

            if self._day_request_count >= self.rpd_limit:
                now_dt   = datetime.now()
                midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                seconds_until_midnight = (midnight.timestamp() + 86400) - now
                wait_time = max(wait_time, seconds_until_midnight + 1)
                logger.warning(
                    f"[{self.model_name}] Daily request limit reached "
                    f"({self._day_request_count}/{self.rpd_limit}). "
                    f"Waiting {wait_time:.0f}s until midnight."
                )

            if self._current_minute_requests() >= self.rpm_limit:
                oldest_min = self._minute_requests[0]
                wait_time  = max(wait_time, (oldest_min + 60) - now + 1)
                logger.info(f"[{self.model_name}] RPM limit reached. Waiting {wait_time:.1f}s")

            if self._current_minute_tokens() + estimated_tokens >= self.tpm_limit:
                if self._minute_tokens:
                    oldest_tok = self._minute_tokens[0][0]
                    wait_time  = max(wait_time, (oldest_tok + 60) - now + 1)
                    logger.info(f"[{self.model_name}] TPM limit reached. Waiting {wait_time:.1f}s")

            if wait_time <= 0:
                break

            logger.info(f"[{self.model_name}] Sleeping {wait_time:.1f}s for rate limit...")
            if self.on_wait:
                self.on_wait(self.model_name, wait_time)
            time.sleep(wait_time)

    def _record_request(self, tokens_used: int):
        now = time.time()
        self._minute_requests.append(now)
        self._minute_tokens.append((now, tokens_used))
        self._day_request_count += 1
        self._save_state()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    @staticmethod
    def _parse_retry_delay(error_message: str) -> float | None:
        match = re.search(r"retryDelay.*?(\d+)s", str(error_message))
        if match:
            return float(match.group(1)) + 2.0
        return None

    def call_with_retry(
        self,
        fn: Callable,
        *args,
        estimated_tokens: int = 500,
        input_tokens: int = 0,
        **kwargs,
    ) -> Any:
        global _gemini_fallback_used, _gemini_fallback_agents

        tpm_check_tokens = input_tokens if input_tokens > 0 else estimated_tokens
        last_exception   = None
        service_unavailable_attempts = 0

        for attempt in range(self.max_retries):
            self._wait_if_needed(tpm_check_tokens)

            try:
                result = fn(*args, **kwargs)
                self._record_request(estimated_tokens)
                return result

            except ResourceExhausted as e:
                last_exception = e
                error_str      = str(e)

                if (self.model_name.startswith("gemini-") and
                        _is_daily_limit_exhaustion(error_str) and
                        not _gemini_fallback_used):
                    logger.error(
                        f"[{self.model_name}] Daily quota exhausted. "
                        f"Triggering Gemini→Gemma fallback."
                    )
                    _gemini_fallback_used = True
                    _gemini_fallback_agents.append(self.model_name)
                    raise GeminiFallbackRequired(
                        f"Gemini daily quota exhausted for {self.model_name}."
                    )

                google_delay = self._parse_retry_delay(error_str)
                backoff      = google_delay if google_delay else self.base_backoff * (2 ** attempt)
                logger.warning(
                    f"[{self.model_name}] 429 on attempt {attempt + 1}/{self.max_retries}. "
                    f"Waiting {backoff:.1f}s."
                )
                if self.on_wait:
                    try:
                        self.on_wait(self.model_name, backoff)
                    except Exception:
                        pass
                time.sleep(backoff)

            except ServiceUnavailable as e:
                last_exception = e
                service_unavailable_attempts += 1

                if service_unavailable_attempts >= self.max_retries_service_unavailable:
                    logger.error(
                        f"[{self.model_name}] ServiceUnavailable retries exhausted "
                        f"({service_unavailable_attempts}/{self.max_retries_service_unavailable})."
                    )
                    if self.model_name.startswith("gemini-") and not _gemini_fallback_used:
                        _gemini_fallback_used = True
                        _gemini_fallback_agents.append(self.model_name)
                        raise GeminiFallbackRequired(
                            f"Gemini ServiceUnavailable exhausted for {self.model_name}."
                        )
                    raise last_exception

                backoff = self.base_backoff * (2 ** attempt)
                logger.warning(
                    f"[{self.model_name}] ServiceUnavailable on attempt "
                    f"{service_unavailable_attempts}/{self.max_retries_service_unavailable}. "
                    f"Backing off {backoff:.1f}s."
                )
                time.sleep(backoff)

            except Exception as e:
                logger.error(f"[{self.model_name}] Non-retryable error: {e}")
                raise

        logger.error(f"[{self.model_name}] All {self.max_retries} retries exhausted.")
        raise last_exception

    def status(self) -> dict:
        self._cleanup_windows()
        return {
            "model":           self.model_name,
            "minute_requests": f"{self._current_minute_requests()}/{self.rpm_limit}",
            "minute_tokens":   f"{self._current_minute_tokens()}/{self.tpm_limit}",
            "day_requests":    f"{self._day_request_count}/{self.rpd_limit}",
        }
