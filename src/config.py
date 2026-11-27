"""
Feature 9: Unified LLM Provider & Dynamic Mock Service.
Supports Gemini, Anthropic, OpenAI-compatible providers, and offline mock execution.
"""

from __future__ import annotations

import ast
import difflib
import logging
import math
import os
import random
import re
import sys
import time
import tempfile
from http import HTTPStatus
from typing import Optional, Dict, Any, List, Tuple, Union

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


_LOGGER = logging.getLogger(__name__)
_DEFAULT_MAX_RETRIES = 3
_MAX_MAX_RETRIES = 5
_SAFE_HTTP_STATUS_CODES = frozenset(status.value for status in HTTPStatus)
_SAFE_STATUS_NAMES = frozenset(status.name for status in HTTPStatus) | frozenset({
    "OK", "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED",
    "NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED",
    "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNAUTHENTICATED",
    "INTERNAL", "UNAVAILABLE", "DATA_LOSS", "UNIMPLEMENTED",
})


class Config:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "mock").lower()
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-2.0-flash")
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    GROQ_API_KEY: Optional[str] = os.getenv("GROQ_API_KEY")
    GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    GROQ_MODEL_NAME: str = os.getenv("GROQ_MODEL_NAME", "llama-3.3-70b-versatile")
    LLM_MAX_RETRIES: int = _DEFAULT_MAX_RETRIES
    LLM_REQUEST_TIMEOUT_SECONDS: Union[str, float] = os.getenv(
        "LLM_REQUEST_TIMEOUT_SECONDS", "60"
    )


_USAGE_LOG: List[Dict[str, Any]] = []
_ATTEMPT_LOG: List[Dict[str, Any]] = []


def _safe_error_type(exc: Exception) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", type(exc).__name__)[:64] or "Exception"


def _safe_exception_attr(source: Any, attr: str) -> Any:
    try:
        return getattr(source, attr, None)
    except Exception:
        return None


def _safe_status_value(value: Any) -> Optional[Union[int, str]]:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            status_code = int(value)
            return status_code if status_code in _SAFE_HTTP_STATUS_CODES else None
        if isinstance(value, str):
            status_text = value.strip().upper()
            if status_text.isdigit():
                status_code = int(status_text)
                return status_code if status_code in _SAFE_HTTP_STATUS_CODES else None
            return status_text if status_text in _SAFE_STATUS_NAMES else None
        status_name = _safe_exception_attr(value, "name")
        if isinstance(status_name, str):
            status_name = status_name.strip().upper()
            return status_name if status_name in _SAFE_STATUS_NAMES else None
    except Exception:
        return None
    return None


def _safe_error_status(exc: Exception) -> Optional[Union[int, str]]:
    sources = [exc]
    sources.append(_safe_exception_attr(exc, "response"))
    for source in sources:
        if source is None:
            continue
        for attr in ("status_code", "code", "http_status", "status", "grpc_status_code"):
            try:
                value = _safe_exception_attr(source, attr)
                if callable(value):
                    value = value()
            except Exception:
                continue
            status = _safe_status_value(value)
            if status is not None:
                return status
    return None


def _llm_request_timeout_seconds() -> float:
    try:
        timeout_seconds = float(Config.LLM_REQUEST_TIMEOUT_SECONDS)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "LLM_REQUEST_TIMEOUT_SECONDS must be a positive finite number"
        ) from exc
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(
            "LLM_REQUEST_TIMEOUT_SECONDS must be a positive finite number"
        )
    return timeout_seconds


class LLMProviderError(RuntimeError):
    """Sanitized provider-call failure retaining the original exception as its cause."""

    def __init__(
        self,
        provider: str,
        model: str,
        original_exception: Exception,
        attempts: int,
    ):
        self.provider = provider
        self.model = model
        self.original_exception = original_exception
        self.attempts = attempts
        self.error_type = _safe_error_type(original_exception)
        self.status = _safe_error_status(original_exception)
        self.retryable = _is_transient_error(original_exception)
        status_text = f", status={self.status}" if self.status is not None else ""
        super().__init__(
            f"{provider} request failed after {attempts} attempt(s): "
            f"{self.error_type}{status_text}"
        )


class ProviderConfigurationError(ValueError):
    """Sanitized provider configuration failure (for example, missing credentials)."""

    def __init__(self, provider: str, model: str, message: str):
        self.provider = provider
        self.model = model
        super().__init__(message)


class ProviderDependencyError(ImportError):
    """Sanitized error for a missing provider SDK."""

    def __init__(self, provider: str, model: str, message: str):
        self.provider = provider
        self.model = model
        super().__init__(message)


def reset_usage_stats() -> None:
    """Clear recorded LLM call telemetry."""
    _USAGE_LOG.clear()
    _ATTEMPT_LOG.clear()


def get_usage_stats() -> Dict[str, Any]:
    """Return call and per-attempt telemetry without retaining prompts or responses."""
    successful_calls = sum(1 for item in _USAGE_LOG if item["success"])
    failed_calls = len(_USAGE_LOG) - successful_calls
    total_prompt_chars = sum(item["prompt_chars"] for item in _ATTEMPT_LOG)
    total_output_chars = sum(item["output_chars"] for item in _ATTEMPT_LOG)
    by_model: Dict[str, int] = {}
    for item in _USAGE_LOG:
        key = f"{item['provider']}:{item['model']}"
        by_model[key] = by_model.get(key, 0) + 1
    attempts_by_model: Dict[str, int] = {}
    for item in _ATTEMPT_LOG:
        key = f"{item['provider']}:{item['model']}"
        attempts_by_model[key] = attempts_by_model.get(key, 0) + 1
    return {
        "total_calls": len(_USAGE_LOG),
        "successful_calls": successful_calls,
        "failed_calls": failed_calls,
        "total_attempts": len(_ATTEMPT_LOG),
        "successful_attempts": sum(1 for item in _ATTEMPT_LOG if item["success"]),
        "failed_attempts": sum(1 for item in _ATTEMPT_LOG if not item["success"]),
        "total_latency_ms": round(
            sum(item["elapsed_ms"] for item in _USAGE_LOG), 2
        ),
        "total_attempt_latency_ms": round(
            sum(item["elapsed_ms"] for item in _ATTEMPT_LOG), 2
        ),
        "est_input_tokens": total_prompt_chars // 4,
        "est_output_tokens": total_output_chars // 4,
        "by_model": by_model,
        "attempts_by_model": attempts_by_model,
    }


def _record_attempt(
    provider: str,
    model: str,
    prompt_str: str,
    out_str: str,
    elapsed_ms: float,
    success: bool,
    exc: Optional[Exception] = None,
) -> None:
    _ATTEMPT_LOG.append({
        "provider": provider,
        "model": model,
        "prompt_chars": len(prompt_str),
        "output_chars": len(out_str),
        "elapsed_ms": elapsed_ms,
        "success": success,
        "error_type": _safe_error_type(exc) if exc else None,
        "status": _safe_error_status(exc) if exc else None,
    })


def _record_call(
    provider: str,
    model: str,
    prompt_str: str,
    elapsed_ms: float,
    success: bool,
    attempts: int,
) -> None:
    _USAGE_LOG.append({
        "provider": provider,
        "model": model,
        "prompt_chars": len(prompt_str),
        "elapsed_ms": elapsed_ms,
        "success": success,
        "attempts": attempts,
    })


def _record_usage(provider: str, model: str, prompt_str: str, out_str: str, start_ts: float) -> str:
    elapsed_ms = (time.monotonic() - start_ts) * 1000.0
    _record_attempt(provider, model, prompt_str, out_str, elapsed_ms, success=True)
    _record_call(provider, model, prompt_str, elapsed_ms, success=True, attempts=1)
    return out_str


def _record_provider_setup_failure(
    provider: str,
    model: str,
    prompt_str: str,
    start_ts: float,
    exc: Exception,
) -> None:
    elapsed_ms = (time.monotonic() - start_ts) * 1000.0
    error_type = _safe_error_type(exc)
    status = _safe_error_status(exc)
    _record_call(provider, model, prompt_str, elapsed_ms, success=False, attempts=0)
    _LOGGER.error(
        "LLM provider setup failed provider=%s model=%s attempt=0 "
        "elapsed_ms=%.2f error_type=%s status=%s",
        provider,
        model,
        elapsed_ms,
        error_type,
        status if status is not None else "unknown",
    )


def _provider_timeout_seconds(
    provider: str,
    model: str,
    prompt_str: str,
    start_ts: float,
) -> float:
    try:
        return _llm_request_timeout_seconds()
    except ValueError as exc:
        _record_provider_setup_failure(provider, model, prompt_str, start_ts, exc)
        raise ProviderConfigurationError(provider, model, str(exc)) from exc


def _execute_provider_call(
    provider: str,
    model: str,
    prompt_str: str,
    fn,
    start_ts: float,
    retry_options: Dict[str, Any],
    jitter: bool = False,
) -> str:
    attempt_number = 0

    def tracked_call() -> str:
        nonlocal attempt_number
        attempt_number += 1
        attempt_started = time.monotonic()
        try:
            output = fn()
        except Exception as exc:
            elapsed_ms = (time.monotonic() - attempt_started) * 1000.0
            error_type = _safe_error_type(exc)
            status = _safe_error_status(exc)
            _record_attempt(
                provider, model, prompt_str, "", elapsed_ms, success=False, exc=exc
            )
            _LOGGER.warning(
                "LLM provider attempt failed provider=%s model=%s attempt=%d "
                "elapsed_ms=%.2f error_type=%s status=%s",
                provider,
                model,
                attempt_number,
                elapsed_ms,
                error_type,
                status if status is not None else "unknown",
            )
            raise

        elapsed_ms = (time.monotonic() - attempt_started) * 1000.0
        output = output if isinstance(output, str) else str(output)
        _record_attempt(
            provider, model, prompt_str, output, elapsed_ms, success=True
        )
        _LOGGER.info(
            "LLM provider attempt succeeded provider=%s model=%s attempt=%d "
            "elapsed_ms=%.2f error_type=none status=none",
            provider,
            model,
            attempt_number,
            elapsed_ms,
        )
        return output

    try:
        output = _execute_with_retry(
            tracked_call, jitter=jitter, **retry_options
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start_ts) * 1000.0
        _record_call(
            provider,
            model,
            prompt_str,
            elapsed_ms,
            success=False,
            attempts=attempt_number,
        )
        raise LLMProviderError(
            provider, model, exc, attempts=attempt_number
        ) from exc

    elapsed_ms = (time.monotonic() - start_ts) * 1000.0
    _record_call(
        provider,
        model,
        prompt_str,
        elapsed_ms,
        success=True,
        attempts=attempt_number,
    )
    return output


def _retry_options(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    options = {
        key: kwargs[key]
        for key in ("initial_delay", "backoff_factor")
        if key in kwargs
    }
    configured_limit = kwargs.get("max_retries")
    options["max_retries"] = (
        Config.LLM_MAX_RETRIES
        if configured_limit is None
        else configured_limit
    )
    return options


def _is_transient_error(exc: Exception, _depth: int = 0) -> bool:
    """Determine if an exception is transient (HTTP 429 rate limit or timeout)."""
    # Check chained/wrapped exceptions (e.g. raise APIError from TimeoutError)
    if _depth < 3:
        cause = getattr(exc, "__cause__", None)
        if cause is not None and cause is not exc and _is_transient_error(cause, _depth=_depth + 1):
            return True
        ctx = getattr(exc, "__context__", None)
        if ctx is not None and ctx is not exc and _is_transient_error(ctx, _depth=_depth + 1):
            return True

    # Check status code attributes directly or on response object
    code = (
        _safe_exception_attr(exc, "status_code")
        or _safe_exception_attr(exc, "code")
        or _safe_exception_attr(exc, "http_status")
        or _safe_exception_attr(exc, "status")
        or _safe_exception_attr(exc, "grpc_status_code")
    )
    if callable(code):
        try:
            code = code()
        except Exception:
            code = None

    if code is not None:
        code_name = _safe_exception_attr(code, "name")
        if code_name is not None:
            code_name = str(code_name).upper()
            if any(term in code_name for term in ("RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE", "INTERNAL")):
                return True
            code = _safe_exception_attr(code, "value") or code
        elif isinstance(code, str):
            code_str = code.upper()
            if any(term in code_str for term in ("RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE", "INTERNAL", "RATELIMIT", "RATE_LIMIT")):
                return True

    if isinstance(code, tuple) and len(code) > 0:
        code = code[0]

    if code is None:
        resp = _safe_exception_attr(exc, "response")
        if resp is not None:
            if isinstance(resp, dict):
                code = resp.get("status_code") or resp.get("status") or resp.get("code")
                if code is None and isinstance(resp.get("error"), dict):
                    code = resp["error"].get("code") or resp["error"].get("status")
            else:
                code = (
                    _safe_exception_attr(resp, "status_code")
                    or _safe_exception_attr(resp, "status")
                )
        if callable(code):
            try:
                code = code()
            except Exception:
                code = None
        if isinstance(code, str):
            code_str = code.upper()
            if any(term in code_str for term in ("RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE", "INTERNAL", "RATELIMIT", "RATE_LIMIT")):
                return True

    if code is not None:
        try:
            int_code = int(code)
            # Transient HTTP status codes: 429 (Rate Limit), 408 (Request Timeout), 529 (Site Overloaded),
            # or 5xx server errors (except 501 Not Implemented).
            # Also gRPC status codes: 4 (DEADLINE_EXCEEDED), 8 (RESOURCE_EXHAUSTED), 14 (UNAVAILABLE).
            if int_code in (429, 408, 529, 4, 8, 14) or (500 <= int_code < 600 and int_code != 501):
                return True
            # Non-transient client errors (e.g. 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found)
            if 400 <= int_code < 500:
                return False
        except (ValueError, TypeError):
            pass

    # Check exception class names
    cls_name = exc.__class__.__name__
    if any(transient in cls_name for transient in [
        "RateLimit", "RateLimitError", "APITimeoutError", "TimeoutError",
        "APIConnectionError", "InternalServerError", "DeadlineExceeded",
        "ResourceExhausted", "ServiceUnavailable", "TooManyRequests",
        "GatewayTimeout", "BadGateway", "Overloaded"
    ]):
        return True

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    # Check error message strings with boundary protection
    try:
        details_str = ""
        details_fn = getattr(exc, "details", None)
        if callable(details_fn):
            try:
                details_str = str(details_fn())
            except Exception:
                pass
        status_fn = getattr(exc, "status", None)
        if callable(status_fn):
            try:
                status_val = str(status_fn())
            except Exception:
                status_val = ""
        else:
            status_val = str(status_fn) if status_fn is not None else ""
        code_val = str(code) if code is not None and not callable(code) else ""
        msg = (str(exc) + " " + status_val + " " + code_val + " " + details_str).lower()
    except Exception:
        msg = ""

    transient_keywords = [
        "rate limit", "ratelimit", "rate_limit", "rate-limit",
        "resource exhausted", "resourceexhausted", "resource_exhausted", "resource-exhausted",
        "quota exceeded", "quota_exceeded", "quota-exceeded",
        "timeout", "timed out", "too many requests", "overloaded",
        "unavailable", "server error", "service unavailable", "service-unavailable",
        "deadline exceeded", "deadlineexceeded", "deadline_exceeded", "deadline-exceeded"
    ]
    if any(k in msg for k in transient_keywords):
        return True

    # Status codes with word boundaries in error messages (avoids false positives on ports e.g. 5020 or IDs)
    if re.search(r"\b(429|408|500|502|503|504|529)\b", msg):
        return True

    return False


def _execute_with_retry(
    fn,
    max_retries: Optional[int] = None,
    initial_delay: Optional[float] = None,
    backoff_factor: Optional[float] = None,
    jitter: bool = False,
    **kwargs
) -> Any:
    """Execute callable with exponential backoff retry on transient errors."""
    try:
        retries = (
            max(0, int(max_retries))
            if max_retries is not None
            else _DEFAULT_MAX_RETRIES
        )
    except (ValueError, TypeError):
        retries = _DEFAULT_MAX_RETRIES
    retries = min(retries, _MAX_MAX_RETRIES)
    try:
        delay = max(0.0, float(initial_delay)) if initial_delay is not None else 1.0
    except (ValueError, TypeError):
        delay = 1.0
    try:
        factor = max(1.0, float(backoff_factor)) if backoff_factor is not None else 2.0
    except (ValueError, TypeError):
        factor = 2.0

    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == retries or not _is_transient_error(exc):
                raise
            time.sleep(random.uniform(0.0, delay) if jitter else delay)
            delay *= factor


def call_llm(
    prompt: str,
    system_prompt: str = "",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs
) -> str:
    """Unified multi-provider LLM call supporting Gemini, OpenAI, or Mock mode."""
    prompt_str = "" if prompt is None else str(prompt)
    sys_prompt_str = "" if system_prompt is None else str(system_prompt)

    raw_provider = provider if provider is not None and str(provider).strip() else Config.LLM_PROVIDER
    target_provider = (raw_provider or "mock").strip().lower() if isinstance(raw_provider, str) and raw_provider.strip() else "mock"

    default_model = Config.GROQ_MODEL_NAME if target_provider == "groq" else Config.MODEL_NAME
    raw_model = model if model is not None else default_model
    target_model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else (
        Config.MODEL_NAME.strip() if isinstance(Config.MODEL_NAME, str) and Config.MODEL_NAME.strip() else "gemini-2.0-flash"
    )

    # Parse temperature safely
    parsed_temp = None
    if temperature is not None:
        try:
            parsed_temp = float(temperature)
        except (ValueError, TypeError):
            parsed_temp = temperature

    # Parse max_tokens safely, with fallback to max_output_tokens or max_completion_tokens
    raw_tokens = max_tokens if max_tokens is not None else (
        kwargs.get("max_output_tokens") or kwargs.get("max_completion_tokens")
    )
    parsed_tokens = None
    if raw_tokens is not None:
        try:
            parsed_tokens = int(raw_tokens)
        except (ValueError, TypeError):
            parsed_tokens = raw_tokens

    t_start = time.monotonic()

    if target_provider == "mock":
        try:
            output = _mock_llm_response(prompt_str, sys_prompt_str, **kwargs)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t_start) * 1000.0
            _record_attempt(
                "mock", target_model, prompt_str, "", elapsed_ms,
                success=False, exc=exc,
            )
            _record_call(
                "mock", target_model, prompt_str, elapsed_ms,
                success=False, attempts=1,
            )
            raise
        return _record_usage("mock", target_model, prompt_str, output, t_start)

    if target_provider in ("gemini", "google", "google-genai", "google_genai"):
        api_key = kwargs.get("api_key") or Config.GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY")
        if not api_key or (isinstance(api_key, str) and not api_key.strip()):
            error = ValueError(
                "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable."
            )
            _record_provider_setup_failure(
                "gemini", target_model, prompt_str, t_start, error
            )
            raise ProviderConfigurationError(
                "gemini", target_model, str(error)
            ) from error

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            error = ImportError(
                "Please install `google-genai` or set LLM_PROVIDER=mock in .env"
            )
            _record_provider_setup_failure(
                "gemini", target_model, prompt_str, t_start, exc
            )
            raise ProviderDependencyError(
                "gemini", target_model, str(error)
            ) from exc

        timeout_seconds = _provider_timeout_seconds(
            "gemini", target_model, prompt_str, t_start
        )
        try:
            client = genai.Client(
                api_key=api_key.strip() if isinstance(api_key, str) else api_key,
                http_options={
                    "timeout": max(1, int(timeout_seconds * 1000)),
                    "retry_options": {"attempts": 1},
                },
            )
        except Exception as exc:
            _record_provider_setup_failure(
                "gemini", target_model, prompt_str, t_start, exc
            )
            raise LLMProviderError(
                "gemini", target_model, exc, attempts=0
            ) from exc

        config_kwargs = {}
        effective_sys = sys_prompt_str or kwargs.get("system_instruction", "")
        if effective_sys:
            config_kwargs["system_instruction"] = effective_sys

        if parsed_temp is not None:
            config_kwargs["temperature"] = parsed_temp
        if parsed_tokens is not None:
            config_kwargs["max_output_tokens"] = parsed_tokens

        supported_genai_keys = (
            "top_p", "top_k", "stop_sequences", "candidate_count",
            "presence_penalty", "frequency_penalty", "seed", "response_mime_type"
        )
        for k in supported_genai_keys:
            if k in kwargs:
                config_kwargs[k] = kwargs[k]

        try:
            config = kwargs.get("config") or (
                types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
            )
            call_kwargs = {"model": target_model, "contents": prompt_str}
            if config is not None:
                call_kwargs["config"] = config
        except Exception as exc:
            _record_provider_setup_failure(
                "gemini", target_model, prompt_str, t_start, exc
            )
            raise ProviderConfigurationError(
                "gemini", target_model, "Gemini request configuration is invalid"
            ) from exc

        def _do_gemini_call():
            response = client.models.generate_content(**call_kwargs)
            if response is None:
                return ""
            if isinstance(response, dict):
                if response.get("text"):
                    return str(response["text"])
                candidates = response.get("candidates")
                if candidates and len(candidates) > 0:
                    cand0 = candidates[0]
                    content = cand0.get("content") if isinstance(cand0, dict) else getattr(cand0, "content", None)
                    if content:
                        parts = content.get("parts") if isinstance(content, dict) else getattr(content, "parts", None)
                        if parts:
                            text_parts = [
                                str(p.get("text", "") if isinstance(p, dict) else getattr(p, "text", ""))
                                for p in parts
                                if (p.get("text") if isinstance(p, dict) else getattr(p, "text", None))
                                and not (p.get("thought", False) if isinstance(p, dict) else getattr(p, "thought", False))
                            ]
                            if text_parts:
                                return "".join(text_parts)
                            fallback_parts = [
                                str(p.get("text", "") if isinstance(p, dict) else getattr(p, "text", ""))
                                for p in parts
                                if (p.get("text") if isinstance(p, dict) else getattr(p, "text", None))
                            ]
                            if fallback_parts:
                                return "".join(fallback_parts)
                return ""
            try:
                text = getattr(response, "text", "")
                if text:
                    return str(text)
            except Exception:
                pass
            try:
                candidates = getattr(response, "candidates", None)
                if candidates and len(candidates) > 0:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None)
                    if parts:
                        text_parts = [
                            str(getattr(p, "text", ""))
                            for p in parts
                            if getattr(p, "text", None) and not getattr(p, "thought", False)
                        ]
                        if text_parts:
                            return "".join(text_parts)
                        fallback_parts = [
                            str(getattr(p, "text", ""))
                            for p in parts
                            if getattr(p, "text", None)
                        ]
                        if fallback_parts:
                            return "".join(fallback_parts)
            except Exception:
                pass
            return ""

        return _execute_provider_call(
            "gemini",
            target_model,
            prompt_str,
            _do_gemini_call,
            t_start,
            _retry_options(kwargs),
            jitter=True,
        )

    if target_provider in ("anthropic", "claude"):
        api_key = kwargs.get("api_key") or Config.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")
        if not api_key or (isinstance(api_key, str) and not api_key.strip()):
            error = ValueError(
                "ANTHROPIC_API_KEY is not set. Please set the ANTHROPIC_API_KEY environment variable."
            )
            _record_provider_setup_failure(
                "anthropic", target_model, prompt_str, t_start, error
            )
            raise ProviderConfigurationError(
                "anthropic", target_model, str(error)
            ) from error
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            error = ImportError(
                "Please install `anthropic` or set LLM_PROVIDER=mock in .env"
            )
            _record_provider_setup_failure(
                "anthropic", target_model, prompt_str, t_start, exc
            )
            raise ProviderDependencyError(
                "anthropic", target_model, str(error)
            ) from exc

        timeout_seconds = _provider_timeout_seconds(
            "anthropic", target_model, prompt_str, t_start
        )
        try:
            client = Anthropic(
                api_key=api_key.strip() if isinstance(api_key, str) else api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
        except Exception as exc:
            _record_provider_setup_failure(
                "anthropic", target_model, prompt_str, t_start, exc
            )
            raise LLMProviderError(
                "anthropic", target_model, exc, attempts=0
            ) from exc
        effective_sys = sys_prompt_str or kwargs.get("system_instruction", "")
        anth_kwargs: Dict[str, Any] = {
            "model": target_model,
            "max_tokens": parsed_tokens if isinstance(parsed_tokens, int) else 4096,
            "messages": [{"role": "user", "content": prompt_str}],
        }
        if effective_sys:
            anth_kwargs["system"] = str(effective_sys)
        if parsed_temp is not None:
            anth_kwargs["temperature"] = parsed_temp

        def _do_anthropic_call():
            resp = client.messages.create(**anth_kwargs)
            blocks = getattr(resp, "content", None) or []
            texts = [
                b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                for b in blocks
                if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "text")) == "text"
            ]
            return "".join(texts)

        return _execute_provider_call(
            "anthropic",
            target_model,
            prompt_str,
            _do_anthropic_call,
            t_start,
            _retry_options(kwargs),
        )

    openai_compatible_providers = (
        "openai", "open_ai", "openai-compatible", "openai_compatible", "openai-compat",
        "openrouter", "groq", "deepseek", "localai", "ollama", "vllm"
    )
    if target_provider in openai_compatible_providers:
        raw_base_url = kwargs.get("base_url")
        if not raw_base_url and target_provider in ("openai", "open_ai", "openai-compatible", "openai_compatible", "openai-compat"):
            raw_base_url = Config.OPENAI_BASE_URL
        base_url = raw_base_url.strip() if isinstance(raw_base_url, str) and raw_base_url.strip() else None
        if not base_url:
            provider_base_urls = {
                "openrouter": "https://openrouter.ai/api/v1",
                "groq": Config.GROQ_BASE_URL or "https://api.groq.com/openai/v1",
                "deepseek": "https://api.deepseek.com/v1",
                "ollama": "http://localhost:11434/v1",
                "localai": "http://localhost:8080/v1",
                "vllm": "http://localhost:8000/v1",
            }
            base_url = provider_base_urls.get(target_provider)

        env_key_map = {
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        provider_env_key = os.getenv(env_key_map[target_provider]) if target_provider in env_key_map else None
        if target_provider == "groq":
            provider_env_key = provider_env_key or Config.GROQ_API_KEY

        # Provider-specific key takes precedence over generic Config.OPENAI_API_KEY
        if target_provider in env_key_map:
            api_key = kwargs.get("api_key") or provider_env_key
            if not api_key and target_provider == "openrouter":
                api_key = Config.OPENAI_API_KEY
        elif target_provider in ("ollama", "localai", "vllm"):
            api_key = kwargs.get("api_key") or "dummy_local_key"
        else:
            api_key = kwargs.get("api_key") or Config.OPENAI_API_KEY

        if not api_key or (isinstance(api_key, str) and not api_key.strip()):
            error = ValueError(
                "OPENAI_API_KEY is not set. Please set the OPENAI_API_KEY environment variable."
            )
            _record_provider_setup_failure(
                target_provider, target_model, prompt_str, t_start, error
            )
            raise ProviderConfigurationError(
                target_provider, target_model, str(error)
            ) from error

        try:
            from openai import OpenAI
        except ImportError as exc:
            error = ImportError(
                "Please install `openai` or set LLM_PROVIDER=mock in .env"
            )
            _record_provider_setup_failure(
                target_provider, target_model, prompt_str, t_start, exc
            )
            raise ProviderDependencyError(
                target_provider, target_model, str(error)
            ) from exc

        timeout_seconds = _provider_timeout_seconds(
            target_provider, target_model, prompt_str, t_start
        )
        client_kwargs = {
            "api_key": api_key.strip() if isinstance(api_key, str) else api_key,
            "timeout": timeout_seconds,
            "max_retries": 0,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        if "organization" in kwargs:
            client_kwargs["organization"] = kwargs["organization"]
        try:
            client = OpenAI(**client_kwargs)
        except Exception as exc:
            _record_provider_setup_failure(
                target_provider, target_model, prompt_str, t_start, exc
            )
            raise LLMProviderError(
                target_provider, target_model, exc, attempts=0
            ) from exc

        messages = []
        effective_sys = sys_prompt_str or kwargs.get("system_instruction", "")
        if effective_sys:
            messages.append({"role": "system", "content": str(effective_sys)})
        messages.append({"role": "user", "content": prompt_str})

        create_kwargs = {
            "model": target_model,
            "messages": messages,
        }
        is_reasoning = target_model.startswith(("o1", "o3", "o4")) or any(x in target_model for x in ("/o1", "/o3", "/o4"))
        if is_reasoning:
            if parsed_tokens is not None:
                create_kwargs["max_completion_tokens"] = parsed_tokens
        else:
            if parsed_temp is not None:
                create_kwargs["temperature"] = parsed_temp
            if parsed_tokens is not None:
                create_kwargs["max_tokens"] = parsed_tokens
        if "max_completion_tokens" in kwargs and "max_completion_tokens" not in create_kwargs:
            create_kwargs["max_completion_tokens"] = kwargs["max_completion_tokens"]

        non_openai_keys = (
            "api_key", "base_url", "organization", "max_retries", "initial_delay",
            "backoff_factor", "system_instruction", "max_output_tokens", "config",
            "category", "archetype", "target_file", "target_class", "target_cls",
            "action", "repair_diff", "repair_tests", "is_repair", "spec", "language"
        )
        for k, v in kwargs.items():
            if k not in non_openai_keys:
                create_kwargs[k] = v

        def _do_openai_call():
            response = client.chat.completions.create(**create_kwargs)
            if response:
                choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
                if choices and len(choices) > 0:
                    first_choice = choices[0]
                    msg = first_choice.get("message") if isinstance(first_choice, dict) else getattr(first_choice, "message", None)
                    if msg is not None:
                        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                        if content is not None:
                            if isinstance(content, str) and content:
                                return content
                            if isinstance(content, list):
                                joined = "".join(
                                    c.get("text", "") if isinstance(c, dict) else getattr(c, "text", str(c))
                                    for c in content
                                )
                                if joined:
                                    return joined
                            if content:
                                return str(content)
                    choice_text = first_choice.get("text") if isinstance(first_choice, dict) else getattr(first_choice, "text", None)
                    if choice_text:
                        return str(choice_text)
            return ""

        return _execute_provider_call(
            target_provider,
            target_model,
            prompt_str,
            _do_openai_call,
            t_start,
            _retry_options(kwargs),
        )

    error = ValueError(f"Unknown LLM provider: {target_provider}")
    _record_provider_setup_failure(
        target_provider, target_model, prompt_str, t_start, error
    )
    raise ProviderConfigurationError(
        target_provider, target_model, str(error)
    ) from error


def _find_target_file_on_disk(
    target_file: Optional[str],
    repository_hint: Optional[str] = None,
) -> Optional[str]:
    """Locate the target file path on disk across current directory and temporary locations."""
    if not target_file or not isinstance(target_file, str) or not target_file.strip():
        return None
    target_file = target_file.strip()
    repository_hint = str(repository_hint or "").strip().replace("\\", "/").rstrip("/")
    repository_hint = repository_hint.rsplit("/", 1)[-1]
    for path in [
        target_file,
        os.path.join("src", target_file),
        os.path.abspath(target_file),
    ]:
        if os.path.isfile(path):
            return path

    if repository_hint:
        target_parts = [
            part for part in target_file.replace("\\", "/").split("/")
            if part and part != "."
        ]
        for root in (
            os.path.join("repos", repository_hint),
            repository_hint,
        ):
            candidate = os.path.join(root, *target_parts)
            if os.path.isfile(candidate):
                return candidate

        ignored_dirs = {
            ".git", "__pycache__", ".tox", ".pytest_cache", "node_modules",
            "build", "dist", "tasks",
        }
        for search_root in (".", tempfile.gettempdir()):
            for root, dirs, _ in os.walk(search_root):
                dirs[:] = [d for d in dirs if d not in ignored_dirs]
                if os.path.basename(os.path.normpath(root)).casefold() != repository_hint.casefold():
                    continue
                candidate = os.path.join(root, *target_parts)
                if os.path.isfile(candidate):
                    return candidate

    base = os.path.basename(target_file)
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".tox", ".pytest_cache", ".eggs", "tasks")]
        if base in files:
            return os.path.join(root, base)

    try:
        temp_dir = tempfile.gettempdir()
        with os.scandir(temp_dir) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name.startswith("tmp"):
                    candidate = os.path.join(entry.path, base)
                    if os.path.isfile(candidate):
                        return candidate
                    try:
                        with os.scandir(entry.path) as sub_entries:
                            for sub in sub_entries:
                                if sub.is_file() and sub.name == base:
                                    return sub.path
                    except OSError:
                        pass
    except OSError:
        pass

    return None


def _normalize_archetype(text: Optional[str]) -> str:
    """Normalize archetype name or category prompt snippet to canonical category id."""
    if not text:
        return "new_feature"
    t = str(text).lower().strip()
    if "task specification" in t:
        t = t.split("task specification")[-1]

    if any(k in t for k in [
        "defensive validation & error handling",
        "defensive validation & boundary protection",
        "validation_edge_cases", "validation edge cases",
        "validation-edge-cases", "category: validation_edge_cases",
    ]):
        return "validation_edge_cases"
    if any(k in t for k in [
        "concurrency & state mutation bug fix",
        "state integrity & mutation bug fix",
        "state_bug_fix", "state bug fix", "state-bug-fix",
        "state mutation", "category: state_bug_fix",
    ]):
        return "state_bug_fix"
    if any(k in t for k in [
        "algorithmic refactor & optimization",
        "performance refactor & algorithmic optimization",
        "performance_refactor", "performance refactor",
        "performance-refactor", "algorithmic refactor",
        "category: performance_refactor",
    ]):
        return "performance_refactor"
    if any(k in t for k in [
        "new functional feature",
        "state integrity & feature validation",
        "new_feature", "new feature", "new-feature",
        "category: new_feature",
    ]):
        return "new_feature"
    return "new_feature"


def _generate_mock_diff(target_file: str, target_cls: str, archetype: str, is_repair: bool = False) -> str:
    """Generate a valid unified git diff patch for the specified archetype."""
    target_file = str(target_file).strip() if target_file is not None and str(target_file).strip() else "service.py"
    if os.path.isabs(target_file):
        try:
            target_file = os.path.relpath(target_file, os.getcwd())
        except Exception:
            target_file = os.path.basename(target_file)
    target_file = target_file.lstrip("/\\")
    target_cls = str(target_cls).strip() if target_cls is not None and str(target_cls).strip() else "PaymentProcessor"
    code_snippets = {
        "new_feature": [
            "    def validate_state(self) -> bool:",
            '        """Validate internal state integrity."""',
            "        return True",
            "",
            "    def apply_voucher(self, code: str, discount_pct: float) -> None:",
            '        """Apply discount voucher code."""',
            "        if not isinstance(code, str) or not code.strip():",
            '            raise ValueError("Invalid voucher code")',
            "        if not (0.0 <= discount_pct <= 100.0):",
            '            raise ValueError("Discount percentage must be between 0 and 100")',
            "        self.voucher_code = code.strip()",
            "        self.discount_pct = float(discount_pct)",
        ],
        "validation_edge_cases": [
            "    def validate_input(self, data: dict) -> bool:",
            '        """Defensively validate dictionary input."""',
            "        if not isinstance(data, dict) or not data:",
            '            raise TypeError("Input must be a valid dictionary")',
            '        if "id" not in data:',
            '            raise KeyError("Missing required key")',
            "        return True",
            "",
            "    def sanitize_payload(self, payload: dict) -> dict:",
            '        """Sanitize payload stripping untrusted attributes."""',
            "        if not isinstance(payload, dict):",
            '            raise TypeError("Payload must be a dictionary")',
            "        sanitized = {}",
            "        for k, v in payload.items():",
            "            if isinstance(v, str) and not v.strip():",
            '                raise ValueError("Payload value cannot be blank")',
            '            if not str(k).startswith("_"):',
            "                sanitized[k] = v",
            "        return sanitized",
        ],
        "state_bug_fix": [
            "    def get_safe_state(self) -> dict:",
            '        """Return defensive snapshot of state preventing mutation."""',
            '        state = getattr(self, "_safe_state_store", None)',
            "        if state is None:",
            '            self._safe_state_store = {"status": "active", "version": 1}',
            "        return dict(self._safe_state_store)",
            "",
            "    def update_state(self, key: str, value: object) -> bool:",
            '        """Update state safely with transactional checks."""',
            "        if key is None:",
            '            raise TypeError("Key must be a string")',
            "        if not isinstance(key, str) or not key.strip():",
            '            raise ValueError("Key cannot be empty")',
            '        state = getattr(self, "_safe_state_store", None)',
            "        if state is None:",
            "            self._safe_state_store = {}",
            "        self._safe_state_store[key] = value",
            "        return True",
        ],
        "performance_refactor": [
            "    def build_lookup_index(self) -> int:",
            '        """Build indexed lookup cache for fast retrieval."""',
            '        self._fast_cache = getattr(self, "_fast_cache", {})',
            '        self._fast_cache["default"] = True',
            "        return len(self._fast_cache)",
            "",
            "    def fast_lookup(self, key: str) -> object:",
            '        """Sub-linear indexed lookup."""',
            "        if not isinstance(key, str):",
            '            raise TypeError("Lookup key must be a string")',
            '        cache = getattr(self, "_fast_cache", None)',
            "        if cache is None:",
            "            self.build_lookup_index()",
            "            cache = self._fast_cache",
            "        return cache.get(key, None)",
        ],
    }

    added_lines = code_snippets.get(archetype, code_snippets["new_feature"])
    disk_path = _find_target_file_on_disk(target_file)

    if disk_path and os.path.isfile(disk_path):
        try:
            with open(disk_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            class_idx = None
            for idx, line in enumerate(lines):
                if re.match(rf"^\s*class\s+{re.escape(target_cls)}\b", line):
                    class_idx = idx
                    break
            if class_idx is not None:
                ctx_before = lines[class_idx : class_idx + 2]
                ctx_after = lines[class_idx + 2 : class_idx + 4]
                start_line = class_idx + 1
                orig_count = len(ctx_before) + len(ctx_after)
                new_count = orig_count + len(added_lines)

                diff_parts = [
                    f"--- a/{target_file}",
                    f"+++ b/{target_file}",
                    f"@@ -{start_line},{orig_count} +{start_line},{new_count} @@",
                ]
                for l in ctx_before:
                    diff_parts.append(" " + l.rstrip("\r\n"))
                for l in added_lines:
                    diff_parts.append("+" + l.rstrip("\r\n"))
                for l in ctx_after:
                    diff_parts.append(" " + l.rstrip("\r\n"))
                diff_parts.append("")
                return "\n".join(diff_parts) + "\n"
        except Exception:
            pass

    body = "\n".join("+" + l for l in added_lines)
    return f"""--- a/{target_file}
+++ b/{target_file}
@@ -10,6 +10,{len(added_lines)} @@
{body}
"""


def _generate_mock_test(target_file: str, target_cls: str, archetype: str, is_repair: bool = False) -> str:
    """Generate a valid pytest test file for the specified archetype."""
    target_file = str(target_file).strip() if target_file is not None and str(target_file).strip() else "service.py"
    target_cls = str(target_cls).strip() if target_cls is not None and str(target_cls).strip() else "PaymentProcessor"
    module_name = os.path.splitext(os.path.basename(target_file))[0] or "service"
    dir_name = os.path.dirname(target_file)

    test_bodies = {
        "new_feature": """    def test_feature_execution(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "validate_state") and obj.validate_state())

    def test_feature_boundary(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "apply_voucher"))
        with self.assertRaises(ValueError):
            obj.apply_voucher("", -1.0)
""",
        "validation_edge_cases": """    def test_feature_execution(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "validate_input") and obj.validate_input({"id": 1}))

    def test_feature_boundary(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "sanitize_payload"))
        with self.assertRaises(ValueError):
            obj.sanitize_payload({"name": "   "})
""",
        "state_bug_fix": """    def test_feature_execution(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "get_safe_state"))
        state = obj.get_safe_state()
        self.assertIsInstance(state, dict)

    def test_feature_boundary(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "update_state"))
        with self.assertRaises(ValueError):
            obj.update_state("", "val")
""",
        "performance_refactor": """    def test_feature_execution(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "build_lookup_index") and obj.build_lookup_index() >= 1)

    def test_feature_boundary(self):
        obj = _instantiate()
        self.assertTrue(hasattr(obj, "fast_lookup"))
        with self.assertRaises(TypeError):
            obj.fast_lookup(123)
""",
    }

    body = test_bodies.get(archetype, test_bodies["new_feature"])

    return f"""import unittest
try:
    from {module_name} import {target_cls}
except ImportError:
    import sys
    sys.path.insert(0, ".")
    sys.path.insert(0, "src")
    # Support src/ layout: import from package root, not module subfolder
    if "{dir_name}".startswith("src"):
        sys.path.insert(0, "{dir_name}".split("/src/")[0] if "/src/" in "{dir_name}" else ".")
    from {module_name} import {target_cls}

def _instantiate():
    for args in [("test", {{}}), ("test",), (), ("USD",), (1,)]:
        try:
            return {target_cls}(*args)
        except Exception:
            pass
    try:
        return object.__new__({target_cls})
    except Exception:
        pass
    return None

class TestFeatureValidation(unittest.TestCase):
{body}
if __name__ == "__main__":
    unittest.main()
"""


def _generate_mock_spec(target_file: str, target_cls: str, archetype: str) -> str:
    """Generate realistic SWE-bench task specification for the specified archetype."""
    target_file = str(target_file).strip() if target_file is not None and str(target_file).strip() else "service.py"
    target_cls = str(target_cls).strip() if target_cls is not None and str(target_cls).strip() else "PaymentProcessor"
    if archetype == "validation_edge_cases":
        return f"""# Feature: Defensive Validation & Boundary Protection

## Problem Description
The `{target_cls}` in `{target_file}` requires robust defensive validation to prevent invalid parameters, out-of-bounds numbers, and blank inputs from corrupting system state.

## Business Requirements
- Defensively validate input types and boundary ranges before processing.
- Reject null, blank, whitespace-only, and malformed strings with informative exceptions.
- Ensure all boundary conditions raise explicit exceptions rather than failing silently.

## Input & Output Schema
- `validate_input(data: dict)`: Accepts a dictionary, returns boolean (`bool`) representing validation status.
- `sanitize_payload(payload: dict)`: Accepts dictionary `payload`, returns sanitized dictionary (`dict`).

## Specification & Acceptance Criteria
- Implement `validate_input(data: dict) -> bool` on `{target_cls}` to verify required keys and types.
- Implement `sanitize_payload(payload: dict) -> dict` on `{target_cls}` to strip untrusted attributes.
- Ensure defensive error checking raises standard Python exceptions.

## Edge Cases & Error Handling
- Null, empty dictionary, or non-dict input must raise `TypeError("Input must be a valid dictionary")`.
- Missing required keys in data dictionary must raise `KeyError("Missing required key")`.
- Blank or whitespace-only string values in payload must raise `ValueError("Payload value cannot be blank")`.

## Target Interface
`{target_cls}.validate_input(data: dict) -> bool`
`{target_cls}.sanitize_payload(payload: dict) -> dict`
"""

    if archetype == "state_bug_fix":
        return f"""# Feature: State Integrity & Mutation Bug Fix

## Problem Description
The `{target_cls}` in `{target_file}` experiences state corruption when internal collections or mutable records are modified concurrently or during iterations.

## Business Requirements
- Protect internal collections from inadvertent external mutations.
- Ensure safe iteration and modification of collection references without concurrency side effects.
- Provide defensive snapshot copies when exposing internal state.

## Input & Output Schema
- `get_safe_state()`: Takes no arguments, returns a defensive shallow copy (`dict`) of internal state.
- `update_state(key: str, value: Any)`: Accepts string `key` and value, returns boolean (`bool`).

## Specification & Acceptance Criteria
- Implement `get_safe_state() -> dict` on `{target_cls}` returning an isolated copy of internal state.
- Implement `update_state(key: str, value: Any) -> bool` on `{target_cls}` with transactional safeguards.
- Prevent modifying collections while they are being iterated.

## Edge Cases & Error Handling
- Calling `get_safe_state()` must not allow caller modifications to alter the underlying storage.
- Passing empty or blank `key` to `update_state` must raise `ValueError("Key cannot be empty")`.
- Passing None as key to `update_state` must raise `TypeError("Key must be a string")`.

## Target Interface
`{target_cls}.get_safe_state() -> dict`
`{target_cls}.update_state(key: str, value: Any) -> bool`
"""

    if archetype == "performance_refactor":
        return f"""# Feature: Performance Refactor & Algorithmic Optimization

## Problem Description
The `{target_cls}` in `{target_file}` relies on linear scan lookups that degrade performance. An algorithmic optimization is required to introduce indexed lookups while preserving full backward compatibility.

## Business Requirements
- Replace linear lookups with an indexed dictionary or hash set lookup.
- Ensure all public interfaces and return structures remain completely unchanged.
- Maintain cache consistency when records are added or updated.

## Input & Output Schema
- `build_lookup_index()`: Takes no arguments, returns integer (`int`) count of indexed records.
- `fast_lookup(key: str)`: Accepts string `key`, returns matching value or `None`.

## Specification & Acceptance Criteria
- Implement `build_lookup_index() -> int` on `{target_cls}` to build precomputed lookup caches.
- Implement `fast_lookup(key: str) -> Any` on `{target_cls}` for sub-linear query execution.
- Maintain cache invalidation and rebuild consistency on updates.

## Edge Cases & Error Handling
- Querying a non-existent key in `fast_lookup` must return `None` without raising exceptions.
- Passing non-string key to `fast_lookup` must raise `TypeError("Lookup key must be a string")`.
- Calling `build_lookup_index` multiple times must safely refresh the index idempotently.

## Target Interface
`{target_cls}.build_lookup_index() -> int`
`{target_cls}.fast_lookup(key: str) -> Any`
"""

    # Default to new_feature
    return f"""# Feature: State Integrity & Feature Validation

## Problem Description
The `{target_cls}` in `{target_file}` needs enhanced state validation and optional discount/voucher handling to support safe execution and boundary checks.

## Business Requirements
- Ensure `{target_cls}` maintains robust internal integrity through explicit validation mechanisms.
- Provide secure discount voucher application protecting against invalid or fraudulent percentage values.
- Maintain non-breaking backward compatibility with existing initialization workflows.

## Input & Output Schema
- `validate_state()`: Takes no arguments, returns boolean (`bool`) representing state validity.
- `apply_voucher(code: str, discount_pct: float)`: Accepts string `code` and float `discount_pct`, returns `None`.

## Specification & Acceptance Criteria
- Add state validation support (`validate_state() -> bool`) to `{target_cls}`.
- Support voucher discount configuration with defensive exception guarantees.
- Ensure all boundary cases return predictable exceptions rather than silent failures.

## Edge Cases & Error Handling
- Invalid or empty code parameters (e.g. empty string or whitespace) must raise `ValueError("Invalid voucher code")`.
- Numerical percentages out of range (less than 0.0 or greater than 100.0) must raise `ValueError`.
- Non-string code parameters or non-numeric discount values must raise defensive type exceptions (`ValueError` or `TypeError`).

## Target Interface
`{target_cls}.validate_state() -> bool`
`{target_cls}.apply_voucher(code: str, discount_pct: float) -> None`
"""


def _extract_numbered_lines_from_prompt(prompt_str: str, target_file: str) -> Tuple[List[Tuple[int, str]], bool]:
    """Extract (line_no, line_text) pairs from Tier 2 numbered snippet for target_file."""
    header = f"### File: {target_file}"
    if header not in prompt_str:
        return [], False
    section = prompt_str.split(header, 1)[1]
    if "```" in section:
        parts = section.split("```", 2)
        if len(parts) >= 2:
            block = parts[1]
            # Strip optional language tag on first line
            if "\n" in block:
                block = block.split("\n", 1)[1]
            truncated = "... [truncated" in block
            parsed: List[Tuple[int, str]] = []
            for raw_line in block.splitlines():
                m = re.match(r"^\s*(\d+)\s\|\s?(.*)$", raw_line)
                if m:
                    parsed.append((int(m.group(1)), m.group(2)))
            return parsed, truncated
    return [], False


def _build_append_diff(target_file: str, added_lines: List[str], prompt_str: str = "") -> str:
    """Build a valid unified diff inserting/appending lines using exact context from prompt or disk."""
    if prompt_str:
        numbered, truncated = _extract_numbered_lines_from_prompt(prompt_str, target_file)
        if numbered:
            if not truncated:
                ctx = numbered[-2:] if len(numbered) >= 2 else numbered[-1:]
                start_line = ctx[0][0]
                orig_count = len(ctx)
                new_count = orig_count + len(added_lines)
                parts = [
                    f"--- a/{target_file}",
                    f"+++ b/{target_file}",
                    f"@@ -{start_line},{orig_count} +{start_line},{new_count} @@",
                ]
                parts.extend(" " + txt for _, txt in ctx)
                parts.extend("+" + line for line in added_lines)
                return "\n".join(parts) + "\n"
            else:
                ctx = numbered[:2]
                start_line = ctx[0][0]
                orig_count = len(ctx)
                new_count = orig_count + len(added_lines)
                parts = [
                    f"--- a/{target_file}",
                    f"+++ b/{target_file}",
                    f"@@ -{start_line},{orig_count} +{start_line},{new_count} @@",
                ]
                parts.extend(" " + txt for _, txt in ctx)
                parts.extend("+" + line for line in added_lines)
                return "\n".join(parts) + "\n"

    disk_path = _find_target_file_on_disk(target_file)
    if disk_path and os.path.isfile(disk_path):
        try:
            with open(disk_path, "r", encoding="utf-8", errors="replace") as f:
                orig_lines = f.read().splitlines()
            if orig_lines:
                ctx = orig_lines[-2:] if len(orig_lines) >= 2 else orig_lines[-1:]
                start_line = len(orig_lines) - len(ctx) + 1
                orig_count = len(ctx)
                new_count = orig_count + len(added_lines)
                parts = [
                    f"--- a/{target_file}",
                    f"+++ b/{target_file}",
                    f"@@ -{start_line},{orig_count} +{start_line},{new_count} @@",
                ]
                parts.extend(" " + line for line in ctx)
                parts.extend("+" + line for line in added_lines)
                return "\n".join(parts) + "\n"
        except Exception:
            pass

    body = "\n".join("+" + line for line in added_lines)
    return f"--- /dev/null\n+++ b/{target_file}\n@@ -0,0 +1,{len(added_lines)} @@\n{body}\n"


def _generate_mock_ts_diff(target_file: str, target_cls: str, archetype: str, prompt_str: str = "") -> str:
    """Generate a valid TypeScript unified git diff patch for mock mode."""
    target_file = str(target_file).strip() if target_file else "src/index.ts"
    added = [
        "",
        "export function validateFactoryState(input: string): boolean {",
        '  if (typeof input !== "string" || !input.trim()) {',
        '    throw new Error("Invalid input");',
        "  }",
        "  return true;",
        "}",
    ]
    return _build_append_diff(target_file, added, prompt_str=prompt_str)


def _generate_mock_ts_test(target_file: str, target_cls: str, archetype: str) -> str:
    """Generate a valid TypeScript test file for mock mode."""
    target_file = str(target_file).strip() if target_file else "src/index.ts"
    rel_import = "../" + target_file.lstrip("./")
    return (
        f'import test from "node:test";\n'
        f'import assert from "node:assert/strict";\n'
        f'import * as targetModule from "{rel_import}";\n\n'
        f'test("validateFactoryState validates non-empty string", () => {{\n'
        f'  const validateFactoryState = Reflect.get(targetModule, "validateFactoryState");\n'
        f'  assert.equal(typeof validateFactoryState, "function");\n'
        f'  assert.equal(validateFactoryState("ok"), true);\n'
        f'  assert.throws(() => validateFactoryState("  "), /Invalid input/);\n'
        f'}});\n'
    )


def _generate_mock_cpp_diff(target_file: str, target_cls: str, archetype: str, prompt_str: str = "") -> str:
    """Generate a valid C++ unified git diff patch for mock mode."""
    target_file = str(target_file).strip() if target_file else "include/feature.h"
    added = [
        "",
        "#include <string>",
        "inline bool validate_factory_state(const std::string& input) {",
        "    return !input.empty();",
        "}",
    ]
    return _build_append_diff(target_file, added, prompt_str=prompt_str)


def _generate_mock_cpp_test(target_file: str, target_cls: str, archetype: str) -> str:
    """Generate a standalone C++ test file for mock mode."""
    target_file = str(target_file).strip() if target_file else "include/feature.h"
    return (
        f'#include <cassert>\n'
        f'#include "{target_file}"\n\n'
        f'int main() {{\n'
        f'    assert(validate_factory_state("ok") == true);\n'
        f'    assert(validate_factory_state("") == false);\n'
        f'    return 0;\n'
        f'}}\n'
    )


def _mock_repository_hint(prompt: str) -> str:
    match = re.search(r"(?im)^\s*Repository:\s*([^\r\n]+)", prompt)
    return match.group(1).strip() if match else ""


def _mock_acceptance_profile(language: str, prompt: str, repository_hint: str) -> Optional[str]:
    normalized_repo = re.sub(r"[^a-z0-9]", "", repository_hint.lower())
    normalized_prompt = prompt.lower().replace("\\", "/")

    if language == "python" and (
        "click" in normalized_repo or "src/click/core.py" in normalized_prompt
    ):
        return "click"
    if language == "typescript" and (
        "zod" in normalized_repo
        or "packages/zod/src/v4/classic/schemas.ts" in normalized_prompt
    ):
        return "zod"
    if language == "cpp" and (
        "jsoncpp" in normalized_repo or "include/json/value.h" in normalized_prompt
    ):
        return "jsoncpp"
    return None


def _read_mock_profile_target(target_file: str, repository_hint: str) -> Tuple[str, str]:
    disk_path = _find_target_file_on_disk(target_file, repository_hint=repository_hint)
    if not disk_path:
        raise ValueError(
            f"Offline mock profile could not find {target_file!r} in repository "
            f"{repository_hint or '<unknown>'!r}."
        )
    with open(disk_path, "r", encoding="utf-8", errors="replace") as source_file:
        return disk_path, source_file.read()


def _mock_unified_diff(target_file: str, original: str, updated: str) -> str:
    normalized_path = target_file.replace("\\", "/")
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{normalized_path}",
            tofile=f"b/{normalized_path}",
            n=3,
        )
    )


def _generate_mock_click_diff(target_file: str, repository_hint: str) -> str:
    _, original = _read_mock_profile_target(target_file, repository_hint)
    tree = ast.parse(original)
    command_class = next(
        (
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "Command"
        ),
        None,
    )
    if command_class is None:
        raise ValueError(f"Click mock target {target_file!r} has no Command class.")
    if any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_parameter_names"
        for node in command_class.body
    ):
        raise ValueError("Click mock target already defines Command.get_parameter_names().")

    lines = original.splitlines(keepends=True)
    insertion = (
        "\n"
        "    def get_parameter_names(self):\n"
        '        """Return named parameters in declaration order."""\n'
        "        names = []\n"
        "        for param in self.params:\n"
        '            name = getattr(param, "name", None)\n'
        "            if isinstance(name, str) and name.strip():\n"
        "                names.append(name)\n"
        "        return names\n"
    )
    insert_at = command_class.end_lineno
    updated = "".join(lines[:insert_at]) + insertion + "".join(lines[insert_at:])
    return _mock_unified_diff(target_file, original, updated)


def _generate_mock_zod_diff(target_file: str, repository_hint: str) -> str:
    _, original = _read_mock_profile_target(target_file, repository_hint)
    if not re.search(r"(?m)^\s*export function string\s*\(", original):
        raise ValueError("Zod mock target does not export the classic string() schema factory.")
    if re.search(r"(?m)^\s*export function nonBlankString\s*\(", original):
        raise ValueError("Zod mock target already defines nonBlankString().")
    updated = original
    if updated and not updated.endswith("\n"):
        updated += "\n"
    updated = (
        updated
        + "\n"
        + "export function nonBlankString() {\n"
        + "  return string().trim().min(1);\n"
        + "}\n"
    )
    return _mock_unified_diff(target_file, original, updated)


def _has_local_vitest(source_path: str) -> bool:
    current = os.path.dirname(os.path.abspath(source_path))
    while True:
        bin_dir = os.path.join(current, "node_modules", ".bin")
        if any(os.path.isfile(os.path.join(bin_dir, name)) for name in ("vitest", "vitest.cmd", "vitest.exe")):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _generate_mock_zod_test(
    target_file: str,
    repository_hint: str,
) -> str:
    source_path, _ = _read_mock_profile_target(target_file, repository_hint)
    module_path = "../packages/zod/src/v4/classic/external.ts"
    if _has_local_vitest(source_path):
        return f"""import {{ expect, test }} from "vitest";
import * as schemas from "{module_path}";

test("nonBlankString trims nonblank strings and rejects blank or non-string values", () => {{
  type Factory = () => {{ safeParse(value: unknown): {{ success: boolean; data?: unknown }} }};
  const factory = Reflect.get(schemas, "nonBlankString") as Factory | undefined;
  expect(typeof factory).toBe("function");
  if (typeof factory !== "function") return;

  const schema = factory();
  expect(schema.safeParse(" value ").success).toBe(true);
  expect(schema.safeParse(" value ").data).toBe("value");
  expect(schema.safeParse("  ").success).toBe(false);
  expect(schema.safeParse(1).success).toBe(false);
}});
"""

    return f"""import test from "node:test";
import assert from "node:assert/strict";
import * as schemas from "{module_path}";

test("nonBlankString trims nonblank strings and rejects blank or non-string values", () => {{
  /** @type {{() => {{safeParse(value: unknown): {{success: boolean, data?: unknown}}}} | undefined}} */
  const factory = Reflect.get(schemas, "nonBlankString");
  assert.equal(typeof factory, "function", "nonBlankString export must exist");
  if (typeof factory !== "function") return;

  const schema = factory();
  assert.equal(schema.safeParse(" value ").success, true);
  assert.equal(schema.safeParse(" value ").data, "value");
  assert.equal(schema.safeParse("  ").success, false);
  assert.equal(schema.safeParse(1).success, false);
}});
"""


def _generate_mock_jsoncpp_diff(target_file: str, repository_hint: str) -> str:
    _, original = _read_mock_profile_target(target_file, repository_hint)
    if re.search(r"\bisEmptyStringOrContainer\s*\(", original):
        raise ValueError("JsonCpp mock target already defines isEmptyStringOrContainer().")
    class_match = re.search(
        r"(?m)^\s*class\s+(?:[A-Za-z_][A-Za-z0-9_]*_API\s+)?Value\b[^;{]*\{",
        original,
    )
    if not class_match:
        raise ValueError(f"JsonCpp mock target {target_file!r} has no Json::Value class.")
    access_match = re.search(r"(?m)^[ \t]*public[ \t]*:[ \t]*(?:\r?\n|$)", original[class_match.end():])
    if not access_match:
        raise ValueError("JsonCpp mock target has no public section in Json::Value.")

    insertion_point = class_match.end() + access_match.end()
    next_member_end = original.find("\n", insertion_point)
    if next_member_end < 0:
        next_member_end = len(original)
    next_member = original[insertion_point:next_member_end]
    indent_match = re.match(r"[ \t]*(?=\S)", next_member)
    indent = indent_match.group(0) if indent_match else "  "
    member = (
        f"{indent}bool isEmptyStringOrContainer() const {{\n"
        f"{indent}    if (isNull()) {{\n"
        f"{indent}        return true;\n"
        f"{indent}    }}\n"
        f"{indent}    if (isString()) {{\n"
        f"{indent}        return asString().empty();\n"
        f"{indent}    }}\n"
        f"{indent}    return (isArray() || isObject()) && size() == 0;\n"
        f"{indent}}}\n"
        f"{indent}\n"
    )
    updated = original[:insertion_point] + member + original[insertion_point:]
    return _mock_unified_diff(target_file, original, updated)


def _generate_mock_jsoncpp_test(target_file: str) -> str:
    header_include = target_file.replace("\\", "/")
    if header_include.startswith("include/"):
        header_include = header_include[len("include/"):]
    return f"""#include <string>
#include <type_traits>
#include <utility>
#include "{header_include}"

template <typename...>
struct MakeVoid {{
    typedef void type;
}};

template <typename T, typename = void>
struct HasEmptyStringOrContainer : std::false_type {{}};

template <typename T>
struct HasEmptyStringOrContainer<
    T,
    typename MakeVoid<decltype(std::declval<const T&>().isEmptyStringOrContainer())>::type
> : std::true_type {{}};

template <typename T>
bool verify_empty_string_or_container(std::true_type) {{
    T null_value;
    T empty_string(std::string{{}});
    T nonempty_string(std::string{{"data"}});
    return null_value.isEmptyStringOrContainer()
        && empty_string.isEmptyStringOrContainer()
        && !nonempty_string.isEmptyStringOrContainer();
}}

template <typename T>
bool verify_empty_string_or_container(std::false_type) {{
    return false;
}}

int main() {{
    return verify_empty_string_or_container<Json::Value>(
        HasEmptyStringOrContainer<Json::Value>()
    ) ? 0 : 1;
}}
"""


def _generate_mock_acceptance_spec(profile: str) -> str:
    if profile == "click":
        return """# Feature: Click Command Parameter Name Introspection

## Problem Description
Click commands expose their registered parameters, but callers must currently traverse parameter objects to collect usable names.

## Business Requirements
- Provide an ordered list of nonblank parameter names for a command.
- Ignore missing, non-string, and whitespace-only names without changing the registered parameters.
- Preserve declaration order and duplicate names.

## Input & Output Schema
- `Command.get_parameter_names()`: takes no arguments and returns a new `list[str]`.

## Specification & Acceptance Criteria
- Add `Command.get_parameter_names()` to the `click.core.Command` API.
- Return each nonblank string `name` from `Command.params` in declaration order.
- Do not mutate or replace `Command.params`.

## Edge Cases & Error Handling
- A missing or `None` parameter name is omitted.
- A non-string or whitespace-only name is omitted.
- Duplicate valid names remain duplicated and retain their original order.
"""
    if profile == "zod":
        return """# Feature: Nonblank Zod String Schema

## Problem Description
Consumers need a reusable classic Zod string schema that trims input and rejects values that become empty.

## Business Requirements
- Expose one factory from the classic Zod schema module.
- Accept and trim nonblank strings.
- Reject blank, whitespace-only, and non-string values through normal Zod parsing results.

## Input & Output Schema
- `nonBlankString()`: takes no arguments and returns a classic Zod string schema.
- Parsing returns the trimmed string on success and a failed safe-parse result for invalid input.

## Specification & Acceptance Criteria
- Export `nonBlankString()` from the classic schema API.
- Build it from the existing classic `string()` schema, trimming before enforcing a minimum length of one.
- Preserve the normal Zod `safeParse()` behavior and result shape.

## Edge Cases & Error Handling
- A string with surrounding whitespace is accepted and returned trimmed.
- An empty or whitespace-only string is rejected.
- A non-string input is rejected without throwing from `safeParse()`.
"""
    if profile == "jsoncpp":
        return """# Feature: Json::Value Empty String or Container Query

## Problem Description
Callers need one const query that consistently identifies null values, empty strings, and empty arrays or objects without classifying scalar values as empty.

## Business Requirements
- Add a public, const query to `Json::Value`.
- Treat null values and empty strings as empty.
- Treat arrays and objects as empty only when they have no elements or members.

## Input & Output Schema
- `Json::Value::isEmptyStringOrContainer() const`: takes no arguments and returns `bool`.

## Specification & Acceptance Criteria
- Return `true` for null values, empty strings, empty arrays, and empty objects.
- Return `false` for nonempty strings, nonempty containers, numbers, and booleans.
- Do not mutate the value.

## Edge Cases & Error Handling
- A default-constructed null value is empty.
- An empty string is empty while a nonempty string is not.
- Numeric and boolean scalar values are not empty.
"""
    raise ValueError(f"Unsupported offline mock acceptance profile: {profile}")


def _mock_llm_response(prompt: str, system_prompt: str = "", **kwargs) -> str:
    """Dynamic mock responses adapting to the scanned repository for rapid local offline testing."""
    prompt_str = "" if prompt is None else str(prompt)
    sys_prompt_str = "" if system_prompt is None else str(system_prompt)
    prompt_lower = prompt_str.lower()
    system_lower = sys_prompt_str.lower()

    # Detect target language
    raw_lang = str(kwargs.get("language") or "").strip().lower()
    if not raw_lang:
        if "target language: typescript" in prompt_lower or "language: typescript" in prompt_lower:
            raw_lang = "typescript"
        elif "target language: c++" in prompt_lower or "language: c++" in prompt_lower:
            raw_lang = "cpp"
        elif "target language: go" in prompt_lower or "language: go" in prompt_lower:
            raw_lang = "go"
        elif "target language: rust" in prompt_lower or "language: rust" in prompt_lower:
            raw_lang = "rust"
        elif "target language: java" in prompt_lower or "language: java" in prompt_lower:
            raw_lang = "java"
        else:
            raw_lang = "python"

    # Detect archetype from kwargs or prompt
    raw_cat = kwargs.get("category") or kwargs.get("archetype")
    if raw_cat:
        category = _normalize_archetype(str(raw_cat))
    else:
        cat_match = re.search(r"task category request:\s*([^\r\n]+)", prompt_lower)
        if cat_match:
            category = _normalize_archetype(cat_match.group(1))
        else:
            category = _normalize_archetype(prompt_lower)

    # Detect target file and class from kwargs or prompt if available
    raw_target_file = kwargs.get("target_file")
    target_file = str(raw_target_file).strip() if raw_target_file is not None and str(raw_target_file).strip() else None
    if not target_file:
        if raw_lang == "cpp":
            hdr_match = re.search(
                r"(?:### File:\s*|in [`']?)([a-zA-Z0-9_\-./]+\.(?:h|hpp|hh))[`']?",
                prompt_str,
            )
            file_match = hdr_match or re.search(
                r"in [`']?([a-zA-Z0-9_\-./]+\.(?:cpp|cc|cxx|h|hpp))[`']?",
                prompt_str,
            )
        else:
            file_match = re.search(
                r"(?:### File:\s*|in [`']?)([a-zA-Z0-9_\-./]+\.(?:py|ts|tsx|js|cpp|cc|cxx|h|hpp))[`']?",
                prompt_str,
            )
        if file_match:
            target_file = file_match.group(1).strip()
        elif raw_lang == "typescript":
            target_file = "src/index.ts"
        elif raw_lang == "cpp":
            target_file = "include/feature.h"
        else:
            target_file = "service.py"

    raw_target_cls = kwargs.get("target_class") or kwargs.get("target_cls")
    target_cls = str(raw_target_cls).strip() if raw_target_cls is not None and str(raw_target_cls).strip() else None
    if not target_cls:
        cls_match = re.search(r"(?:class\s+|The\s+[`']?)([A-Z][a-zA-Z0-9_]+)", prompt_str)
        target_cls = cls_match.group(1).strip() if cls_match else "PaymentProcessor"

    repository_hint = _mock_repository_hint(prompt_str)
    profile = _mock_acceptance_profile(raw_lang, prompt_str, repository_hint)
    if profile:
        if category != "new_feature":
            raise ValueError(
                f"Offline mock profile {profile!r} only supports the new_feature category; "
                f"refusing to generate an unrelated {category!r} task."
            )
        target_file = {
            "click": "src/click/core.py",
            "zod": "packages/zod/src/v4/classic/schemas.ts",
            "jsoncpp": "include/json/value.h",
        }[profile]
        target_cls = {"click": "Command", "zod": "ZodString", "jsoncpp": "Value"}[profile]

    def _diff_for_lang(is_repair: bool = False) -> str:
        if profile == "click":
            return _generate_mock_click_diff(target_file, repository_hint)
        if profile == "zod":
            return _generate_mock_zod_diff(target_file, repository_hint)
        if profile == "jsoncpp":
            return _generate_mock_jsoncpp_diff(target_file, repository_hint)
        if raw_lang == "typescript":
            return _generate_mock_ts_diff(target_file, target_cls, category, prompt_str=prompt_str)
        if raw_lang == "cpp":
            return _generate_mock_cpp_diff(target_file, target_cls, category, prompt_str=prompt_str)
        if raw_lang in ("go", "rust", "java"):
            return f"--- a/{target_file}\n+++ b/{target_file}\n@@ -1,1 +1,3 @@\n+// feature: {category}\n+func validate_state() bool {{ return true }}\n"
        return _generate_mock_diff(target_file, target_cls, category, is_repair=is_repair)

    def _test_for_lang(is_repair: bool = False) -> str:
        if profile == "click":
            return """import unittest
from types import SimpleNamespace

from click.core import Command


class TestCommandParameterNames(unittest.TestCase):
    def test_returns_ordered_nonblank_names_without_mutating_params(self):
        command = Command("example")
        command.params = [
            SimpleNamespace(name="verbose"),
            SimpleNamespace(name=None),
            SimpleNamespace(name="   "),
            SimpleNamespace(name=17),
            SimpleNamespace(name="input"),
            SimpleNamespace(name="verbose"),
        ]
        original_params = command.params

        self.assertEqual(
            command.get_parameter_names(),
            ["verbose", "input", "verbose"],
        )
        self.assertIs(command.params, original_params)


if __name__ == "__main__":
    unittest.main()
"""
        if profile == "zod":
            return _generate_mock_zod_test(target_file, repository_hint)
        if profile == "jsoncpp":
            return _generate_mock_jsoncpp_test(target_file)
        if raw_lang == "typescript":
            return _generate_mock_ts_test(target_file, target_cls, category)
        if raw_lang == "cpp":
            return _generate_mock_cpp_test(target_file, target_cls, category)
        if raw_lang == "go":
            return "package main\n\nimport \"testing\"\n\nfunc TestFeaturePatch(t *testing.T) {\n    t.Log(\"ok\")\n}\n"
        if raw_lang == "rust":
            return "#[test]\nfn test_feature_patch() {\n    assert!(true);\n}\n"
        if raw_lang == "java":
            return "import org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.assertTrue;\n\nclass FeaturePatchTest {\n    @Test\n    void testFeaturePatch() { assertTrue(true); }\n}\n"
        return _generate_mock_test(target_file, target_cls, category, is_repair=is_repair)

    # Check system prompt first to unambiguously distinguish agents
    if any(k in system_lower for k in [
        "solution/solution.diff", "ground-truth reference author", "coder_agent", "feature 3"
    ]):
        is_repair = ("previous solution diff:" in prompt_lower or
                     "corrected unified git diff" in prompt_lower or
                     "repair_diff" in prompt_lower or
                     bool(kwargs.get("repair_diff")) or
                     bool(kwargs.get("is_repair")))
        return _diff_for_lang(is_repair=is_repair)

    if any(k in system_lower for k in [
        "test_feature_patch", "acceptance test synthesizer", "test_agent", "qa engineer", "feature 4"
    ]):
        is_repair = ("previous test file:" in prompt_lower or
                     "corrected pytest test file" in prompt_lower or
                     "repair_tests" in prompt_lower or
                     bool(kwargs.get("repair_tests")) or
                     bool(kwargs.get("is_repair")))
        return _test_for_lang(is_repair=is_repair)

    if any(k in system_lower for k in [
        "spec_generator", "task specification designer", "feature 2", "instruction.md"
    ]):
        if profile:
            return _generate_mock_acceptance_spec(profile)
        return _generate_mock_spec(target_file, target_cls, category)

    if any(k in system_lower for k in [
        "benchmark_evaluator", "evaluator_prompt", "feature 8", "benchmark evaluator"
    ]):
        return _diff_for_lang(is_repair=False)

    # If system prompt is empty or generic, check prompt keywords
    if any(k in prompt_lower for k in [
        "swe-bench evaluation benchmark", "evaluator_prompt", "participating in a swe-bench"
    ]):
        return _diff_for_lang(is_repair=False)

    if ("previous test file:" in prompt_lower or
        "corrected pytest test file" in prompt_lower or
        prompt_lower.strip().startswith("repair_tests") or
        "repair_tests" in prompt_lower or
        "repair test" in prompt_lower or
        bool(kwargs.get("repair_tests")) or
        kwargs.get("action") == "repair_tests"):
        return _test_for_lang(is_repair=True)

    if ("previous solution diff:" in prompt_lower or
        "corrected unified git diff" in prompt_lower or
        prompt_lower.strip().startswith("repair_diff") or
        "repair_diff" in prompt_lower or
        "repair diff" in prompt_lower or
        bool(kwargs.get("repair_diff")) or
        kwargs.get("action") == "repair_diff"):
        return _diff_for_lang(is_repair=True)

    if "return only valid python pytest code" in prompt_lower or "return only valid" in prompt_lower and "test code" in prompt_lower:
        return _test_for_lang(is_repair=False)

    if "return only the unified git diff" in prompt_lower or "unified git diff" in prompt_lower:
        return _diff_for_lang(is_repair=False)

    if ("task category request" in prompt_lower or
        "specification" in prompt_lower or
        "instruction.md" in prompt_lower or
        "spec_generator" in prompt_lower or
        bool(kwargs.get("spec")) or
        kwargs.get("action") in ("spec", "generate_spec") or
        bool(kwargs.get("category")) or
        bool(kwargs.get("archetype")) or
        bool(kwargs.get("target_file")) or
        bool(kwargs.get("target_class")) or
        bool(kwargs.get("target_cls"))):
        return _generate_mock_spec(target_file, target_cls, category)

    return "Mock response for query."
