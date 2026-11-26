"""
Feature 9: Unified LLM Provider & Dynamic Mock Service.
Supports: 'gemini', 'openai', and 'mock' (dynamic mock engine for rapid local testing without API keys).
"""

from __future__ import annotations

import os
import re
import sys
import time
import tempfile
from typing import Optional, Dict, Any, List, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "mock").lower()
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-2.0-flash")
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL: Optional[str] = os.getenv("OPENAI_BASE_URL")
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")


_USAGE_LOG: List[Dict[str, Any]] = []


def reset_usage_stats() -> None:
    """Clear recorded LLM call telemetry."""
    _USAGE_LOG.clear()


def get_usage_stats() -> Dict[str, Any]:
    """Return aggregated call counts, latency, and estimated token usage across all LLM calls."""
    total_calls = len(_USAGE_LOG)
    total_ms = sum(item["latency_ms"] for item in _USAGE_LOG)
    total_prompt_chars = sum(item["prompt_chars"] for item in _USAGE_LOG)
    total_output_chars = sum(item["output_chars"] for item in _USAGE_LOG)
    by_model: Dict[str, int] = {}
    for item in _USAGE_LOG:
        key = f"{item['provider']}:{item['model']}"
        by_model[key] = by_model.get(key, 0) + 1
    return {
        "total_calls": total_calls,
        "total_latency_ms": round(total_ms, 2),
        "est_input_tokens": total_prompt_chars // 4,
        "est_output_tokens": total_output_chars // 4,
        "by_model": by_model,
    }


def _record_usage(provider: str, model: str, prompt_str: str, out_str: str, start_ts: float) -> str:
    _USAGE_LOG.append({
        "provider": provider,
        "model": model,
        "prompt_chars": len(prompt_str),
        "output_chars": len(out_str),
        "latency_ms": (time.monotonic() - start_ts) * 1000.0,
    })
    return out_str


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
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(exc, "http_status", None)
        or getattr(exc, "status", None)
        or getattr(exc, "grpc_status_code", None)
    )
    if callable(code):
        try:
            code = code()
        except Exception:
            code = None

    if code is not None:
        if hasattr(code, "name"):
            code_name = str(code.name).upper()
            if any(term in code_name for term in ("RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE", "INTERNAL")):
                return True
            code = getattr(code, "value", code)
        elif isinstance(code, str):
            code_str = code.upper()
            if any(term in code_str for term in ("RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE", "INTERNAL", "RATELIMIT", "RATE_LIMIT")):
                return True

    if isinstance(code, tuple) and len(code) > 0:
        code = code[0]

    if code is None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            if isinstance(resp, dict):
                code = resp.get("status_code") or resp.get("status") or resp.get("code")
                if code is None and isinstance(resp.get("error"), dict):
                    code = resp["error"].get("code") or resp["error"].get("status")
            else:
                code = getattr(resp, "status_code", None) or getattr(resp, "status", None)
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
    **kwargs
) -> Any:
    """Execute callable with exponential backoff retry on transient errors."""
    try:
        retries = max(0, int(max_retries)) if max_retries is not None else 3
    except (ValueError, TypeError):
        retries = 3
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
            time.sleep(delay)
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

    raw_model = model if model is not None else Config.MODEL_NAME
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
        return _record_usage("mock", target_model, prompt_str, _mock_llm_response(prompt_str, sys_prompt_str, **kwargs), t_start)

    if target_provider in ("gemini", "google", "google-genai", "google_genai"):
        api_key = kwargs.get("api_key") or Config.GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY")
        if not api_key or (isinstance(api_key, str) and not api_key.strip()):
            raise ValueError("GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable.")

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ImportError("Please install `google-genai` or set LLM_PROVIDER=mock in .env")

        client = genai.Client(api_key=api_key.strip() if isinstance(api_key, str) else api_key)

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

        config = kwargs.get("config") or (types.GenerateContentConfig(**config_kwargs) if config_kwargs else None)
        call_kwargs = {"model": target_model, "contents": prompt_str}
        if config is not None:
            call_kwargs["config"] = config

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

        return _record_usage("gemini", target_model, prompt_str, _execute_with_retry(_do_gemini_call, **kwargs), t_start)

    if target_provider in ("anthropic", "claude"):
        api_key = kwargs.get("api_key") or Config.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")
        if not api_key or (isinstance(api_key, str) and not api_key.strip()):
            raise ValueError("ANTHROPIC_API_KEY is not set. Please set the ANTHROPIC_API_KEY environment variable.")
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("Please install `anthropic` or set LLM_PROVIDER=mock in .env")

        client = Anthropic(api_key=api_key.strip() if isinstance(api_key, str) else api_key)
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

        return _record_usage("anthropic", target_model, prompt_str, _execute_with_retry(_do_anthropic_call, **kwargs), t_start)

    openai_compatible_providers = (
        "openai", "open_ai", "openai-compatible", "openai_compatible", "openai-compat",
        "openrouter", "groq", "deepseek", "localai", "ollama", "vllm"
    )
    if target_provider in openai_compatible_providers:
        raw_base_url = kwargs.get("base_url") or Config.OPENAI_BASE_URL
        base_url = raw_base_url.strip() if isinstance(raw_base_url, str) and raw_base_url.strip() else None
        if not base_url:
            provider_base_urls = {
                "openrouter": "https://openrouter.ai/api/v1",
                "groq": "https://api.groq.com/openai/v1",
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
        api_key = kwargs.get("api_key") or Config.OPENAI_API_KEY or provider_env_key
        if target_provider in ("ollama", "localai", "vllm") and not api_key:
            api_key = "dummy_local_key"

        if not api_key or (isinstance(api_key, str) and not api_key.strip()):
            raise ValueError("OPENAI_API_KEY is not set. Please set the OPENAI_API_KEY environment variable.")

        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Please install `openai` or set LLM_PROVIDER=mock in .env")

        client_kwargs = {"api_key": api_key.strip() if isinstance(api_key, str) else api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        if "organization" in kwargs:
            client_kwargs["organization"] = kwargs["organization"]
        client = OpenAI(**client_kwargs)

        messages = []
        effective_sys = sys_prompt_str or kwargs.get("system_instruction", "")
        if effective_sys:
            messages.append({"role": "system", "content": str(effective_sys)})
        messages.append({"role": "user", "content": prompt_str})

        create_kwargs = {
            "model": target_model,
            "messages": messages,
        }
        if target_model.startswith(("o1", "o3")):
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

        return _record_usage(target_provider, target_model, prompt_str, _execute_with_retry(_do_openai_call, **kwargs), t_start)

    raise ValueError(f"Unknown LLM provider: {target_provider}")


def _find_target_file_on_disk(target_file: Optional[str]) -> Optional[str]:
    """Locate the target file path on disk across current directory and temporary locations."""
    if not target_file or not isinstance(target_file, str) or not target_file.strip():
        return None
    target_file = target_file.strip()
    for path in [
        target_file,
        os.path.join("src", target_file),
        os.path.abspath(target_file),
    ]:
        if os.path.isfile(path):
            return path

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
        f'import {{ validateFactoryState }} from "{rel_import}";\n\n'
        f'test("validateFactoryState validates non-empty string", () => {{\n'
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
                r"in [`']?([a-zA-Z0-9_\-./]+\.(?:py|ts|tsx|js|cpp|cc|cxx|h|hpp))[`']?",
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

    def _diff_for_lang(is_repair: bool = False) -> str:
        if raw_lang == "typescript":
            return _generate_mock_ts_diff(target_file, target_cls, category, prompt_str=prompt_str)
        if raw_lang == "cpp":
            return _generate_mock_cpp_diff(target_file, target_cls, category, prompt_str=prompt_str)
        return _generate_mock_diff(target_file, target_cls, category, is_repair=is_repair)

    def _test_for_lang(is_repair: bool = False) -> str:
        if raw_lang == "typescript":
            return _generate_mock_ts_test(target_file, target_cls, category)
        if raw_lang == "cpp":
            return _generate_mock_cpp_test(target_file, target_cls, category)
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
