"""
Unit tests for Feature 9: Multi-Provider LLM & Dynamic Mock Service.
Executes completely offline with mocked API clients and dynamic mock engine verification.
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from src.config import (
    Config, call_llm, _mock_llm_response, _is_transient_error,
    _execute_with_retry, _generate_mock_diff, _generate_mock_test,
    _generate_mock_spec, _find_target_file_on_disk
)


class TestCallLLMProviderBasics(unittest.TestCase):
    """Test provider resolution, overrides, and basic configuration."""

    def setUp(self):
        self.orig_provider = Config.LLM_PROVIDER
        self.orig_model = Config.MODEL_NAME
        self.orig_gemini_key = Config.GEMINI_API_KEY
        self.orig_openai_key = Config.OPENAI_API_KEY
        self.orig_openai_base_url = Config.OPENAI_BASE_URL

    def tearDown(self):
        Config.LLM_PROVIDER = self.orig_provider
        Config.MODEL_NAME = self.orig_model
        Config.GEMINI_API_KEY = self.orig_gemini_key
        Config.OPENAI_API_KEY = self.orig_openai_key
        Config.OPENAI_BASE_URL = self.orig_openai_base_url

    def test_default_mock_provider(self):
        Config.LLM_PROVIDER = "mock"
        response = call_llm(prompt="Hello from test", system_prompt="")
        self.assertIsInstance(response, str)
        self.assertTrue(len(response) > 0)

    def test_per_call_provider_override_to_mock(self):
        Config.LLM_PROVIDER = "openai"
        Config.OPENAI_API_KEY = None
        # Explicit provider="mock" should bypass OpenAI client and return mock response
        response = call_llm(prompt="Generate patch", provider="mock")
        self.assertIsInstance(response, str)

    def test_provider_case_insensitivity_and_whitespace(self):
        Config.LLM_PROVIDER = "mock"
        response = call_llm(prompt="Hello", provider="  MOCK  ")
        self.assertIsInstance(response, str)

    def test_provider_alias_google(self):
        Config.GEMINI_API_KEY = "dummy_key"
        mock_genai = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value.text = "google alias works"

        modules_patch = {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }
        with patch.dict(sys.modules, modules_patch):
            out = call_llm(prompt="Hello", provider="google")
        self.assertEqual(out, "google alias works")

    def test_unknown_provider_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            call_llm(prompt="Test", provider="anthropic-unsupported")
        self.assertIn("Unknown LLM provider", str(ctx.exception))


class TestGeminiProvider(unittest.TestCase):
    """Test Google Gemini SDK integration with mock client and native GenerateContentConfig."""

    def setUp(self):
        self.orig_gemini_key = Config.GEMINI_API_KEY

    def tearDown(self):
        Config.GEMINI_API_KEY = self.orig_gemini_key

    def test_gemini_missing_api_key_raises_error(self):
        Config.GEMINI_API_KEY = None
        with self.assertRaises(ValueError) as ctx:
            call_llm(prompt="Test prompt", provider="gemini")
        self.assertIn("GEMINI_API_KEY is not set", str(ctx.exception))

    def test_gemini_whitespace_api_key_raises_error(self):
        Config.GEMINI_API_KEY = "   "
        with self.assertRaises(ValueError) as ctx:
            call_llm(prompt="Test prompt", provider="gemini")
        self.assertIn("GEMINI_API_KEY is not set", str(ctx.exception))

    def test_gemini_uses_config_api_key_when_kwarg_omitted(self):
        Config.GEMINI_API_KEY = "env_gemini_key_789"
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value.text = "output from gemini"

        modules_patch = {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        }
        with patch.dict(sys.modules, modules_patch):
            out = call_llm(prompt="Test prompt", provider="gemini", initial_delay=0.0)
        self.assertEqual(out, "output from gemini")
        mock_genai.Client.assert_called_once_with(api_key="env_gemini_key_789", http_options={"timeout": 60000})

    def test_gemini_extended_config_kwargs(self):
        Config.GEMINI_API_KEY = "test_key"
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value.text = "json output"

        mock_google = MagicMock()
        mock_google.genai = mock_genai

        modules_patch = {
            "google": mock_google,
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        }
        with patch.dict(sys.modules, modules_patch):
            call_llm(
                prompt="Produce JSON",
                provider="gemini",
                seed=42,
                presence_penalty=0.5,
                response_mime_type="application/json",
                initial_delay=0.0,
            )
        mock_types.GenerateContentConfig.assert_called_once_with(
            presence_penalty=0.5,
            seed=42,
            response_mime_type="application/json",
        )

    def test_gemini_client_and_native_generate_content_config(self):
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = "Gemini generated patch content"
        mock_client.models.generate_content.return_value = mock_response

        # Mock GenerateContentConfig
        dummy_config = MagicMock()
        mock_types.GenerateContentConfig.return_value = dummy_config

        mock_google = MagicMock()
        mock_google.genai = mock_genai
        mock_genai.types = mock_types

        modules_patch = {
            "google": mock_google,
            "google.genai": mock_genai,
            "google.genai.types": mock_types,
        }

        with patch.dict(sys.modules, modules_patch):
            out = call_llm(
                prompt="Write a feature diff",
                system_prompt="You are a principal engineer.",
                provider="gemini",
                model="gemini-2.0-flash",
                temperature=0.4,
                max_tokens=2048,
                api_key="test_gemini_api_key_123",
                initial_delay=0.0,
            )

        self.assertEqual(out, "Gemini generated patch content")
        mock_genai.Client.assert_called_once_with(api_key="test_gemini_api_key_123", http_options={"timeout": 60000})
        mock_types.GenerateContentConfig.assert_called_once_with(
            system_instruction="You are a principal engineer.",
            temperature=0.4,
            max_output_tokens=2048,
        )
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-2.0-flash",
            contents="Write a feature diff",
            config=dummy_config,
        )

    def test_gemini_import_error_handling(self):
        Config.GEMINI_API_KEY = "dummy_key"
        with patch.dict(sys.modules, {"google.genai": None}):
            # Ensure importing google.genai triggers ImportError
            with patch("builtins.__import__", side_effect=ImportError("No module named 'google.genai'")):
                with self.assertRaises(ImportError) as ctx:
                    call_llm(prompt="Test", provider="gemini")
                self.assertIn("google-genai", str(ctx.exception))

    def test_gemini_property_value_error_fallback(self):
        Config.GEMINI_API_KEY = "dummy_key"
        mock_genai = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        class DummyResponse:
            @property
            def text(self):
                raise ValueError("Response blocked by safety filters")
            @property
            def candidates(self):
                mock_part = MagicMock(text="fallback from candidate part")
                mock_content = MagicMock(parts=[mock_part])
                return [MagicMock(content=mock_content)]

        mock_client.models.generate_content.return_value = DummyResponse()

        modules_patch = {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }
        with patch.dict(sys.modules, modules_patch):
            out = call_llm(prompt="Test fallback", provider="gemini")
        self.assertEqual(out, "fallback from candidate part")


class TestOpenAIProvider(unittest.TestCase):
    """Test OpenAI SDK integration, OPENAI_BASE_URL support, and chat completion options."""

    def setUp(self):
        self.orig_openai_key = Config.OPENAI_API_KEY
        self.orig_openai_base_url = Config.OPENAI_BASE_URL

    def tearDown(self):
        Config.OPENAI_API_KEY = self.orig_openai_key
        Config.OPENAI_BASE_URL = self.orig_openai_base_url

    def test_openai_missing_api_key_raises_error(self):
        Config.OPENAI_API_KEY = None
        with self.assertRaises(ValueError) as ctx:
            call_llm(prompt="Test prompt", provider="openai")
        self.assertIn("OPENAI_API_KEY is not set", str(ctx.exception))

    def test_openai_whitespace_api_key_raises_error(self):
        Config.OPENAI_API_KEY = "   "
        with self.assertRaises(ValueError) as ctx:
            call_llm(prompt="Test prompt", provider="openai")
        self.assertIn("OPENAI_API_KEY is not set", str(ctx.exception))

    def test_openai_import_error_handling(self):
        Config.OPENAI_API_KEY = "dummy_key"
        with patch.dict(sys.modules, {"openai": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'openai'")):
                with self.assertRaises(ImportError) as ctx:
                    call_llm(prompt="Test", provider="openai")
                self.assertIn("openai", str(ctx.exception))

    def test_openai_empty_choices_handled_gracefully(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = []
        mock_client.chat.completions.create.return_value = mock_response

        Config.OPENAI_API_KEY = "dummy_key"
        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(prompt="Test", provider="openai", initial_delay=0.0)
        self.assertEqual(out, "")

    def test_openai_client_chat_completions(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "OpenAI generated code"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(
                prompt="Solve SWE issue",
                system_prompt="You are an autonomous AI.",
                provider="openai",
                model="gpt-4o",
                temperature=0.2,
                max_tokens=1500,
                api_key="test_openai_api_key_456",
                initial_delay=0.0,
            )

        self.assertEqual(out, "OpenAI generated code")
        mock_openai_module.OpenAI.assert_called_once_with(api_key="test_openai_api_key_456", timeout=60.0)
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an autonomous AI."},
                {"role": "user", "content": "Solve SWE issue"},
            ],
            temperature=0.2,
            max_tokens=1500,
        )

    def test_openai_honors_openai_base_url(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "Custom endpoint response"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        Config.OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
        Config.OPENAI_API_KEY = "groq_key"

        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(
                prompt="Fast inference",
                provider="openai",
                model="llama-3.3-70b",
                initial_delay=0.0,
            )

        self.assertEqual(out, "Custom endpoint response")
        mock_openai_module.OpenAI.assert_called_once_with(
            api_key="groq_key",
            timeout=60.0,
            base_url="https://api.groq.com/openai/v1",
        )

    def test_openai_per_call_base_url_override(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="LocalAI result"))
        ]

        Config.OPENAI_API_KEY = "local_key"
        Config.OPENAI_BASE_URL = "http://initial-base-url"

        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(
                prompt="Local model call",
                provider="openai",
                base_url="http://localhost:8000/v1",
                initial_delay=0.0,
            )

        self.assertEqual(out, "LocalAI result")
        mock_openai_module.OpenAI.assert_called_once_with(
            api_key="local_key",
            timeout=60.0,
            base_url="http://localhost:8000/v1",
        )

    def test_openai_base_url_whitespace_stripped(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="Stripped base url"))
        ]

        Config.OPENAI_API_KEY = "key"
        Config.OPENAI_BASE_URL = "   "

        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(prompt="Test", provider="openai")

        self.assertEqual(out, "Stripped base url")
        mock_openai_module.OpenAI.assert_called_once_with(api_key="key", timeout=60.0)

    def test_openai_choice_text_legacy_fallback(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client
        legacy_choice = MagicMock(message=None, text="legacy text completion")
        mock_client.chat.completions.create.return_value.choices = [legacy_choice]

        Config.OPENAI_API_KEY = "key"
        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(prompt="Legacy", provider="openai")

        self.assertEqual(out, "legacy text completion")


class TestRetryLogic(unittest.TestCase):
    """Test transient retry logic with exponential backoff on HTTP 429 and timeouts."""

    def test_is_transient_error_detection(self):
        # HTTP 429 rate limit
        e_429 = Exception("Resource has been exhausted (e.g. check quota) 429 Too Many Requests")
        self.assertTrue(_is_transient_error(e_429))

        # Status code attribute
        class RateLimitError(Exception):
            status_code = 429
        self.assertTrue(_is_transient_error(RateLimitError()))

        # Timeout
        self.assertTrue(_is_transient_error(TimeoutError("Operation timed out")))

        # Connection error
        self.assertTrue(_is_transient_error(ConnectionError("Connection reset by peer")))

        # Non-transient errors
        self.assertFalse(_is_transient_error(ValueError("Invalid argument")))
        self.assertFalse(_is_transient_error(KeyError("Missing key")))
        class BadRequestError(Exception):
            status_code = 400
        self.assertFalse(_is_transient_error(BadRequestError()))

    def test_retry_on_rate_limit_eventual_success(self):
        attempts = 0

        def flaky_call():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise Exception("HTTP 429: Too Many Requests - Rate limit exceeded")
            return "Success after retries"

        sleep_mock = MagicMock()
        with patch("time.sleep", sleep_mock):
            result = _execute_with_retry(
                flaky_call,
                max_retries=3,
                initial_delay=1.0,
                backoff_factor=2.0,
            )

        self.assertEqual(result, "Success after retries")
        self.assertEqual(attempts, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        # Check exponential backoff delays: 1.0, 2.0
        sleep_mock.assert_any_call(1.0)
        sleep_mock.assert_any_call(2.0)

    def test_retry_on_timeout_eventual_success(self):
        attempts = 0

        def timeout_call():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("Read timeout occurred")
            return "Recovered from timeout"

        sleep_mock = MagicMock()
        with patch("time.sleep", sleep_mock):
            result = _execute_with_retry(
                timeout_call,
                max_retries=2,
                initial_delay=0.5,
                backoff_factor=2.0,
            )

        self.assertEqual(result, "Recovered from timeout")
        self.assertEqual(attempts, 2)
        sleep_mock.assert_called_once_with(0.5)

    def test_retry_exhaustion_raises_original_exception(self):
        def always_failing():
            raise Exception("Persistent HTTP 429 rate limit")

        sleep_mock = MagicMock()
        with patch("time.sleep", sleep_mock):
            with self.assertRaises(Exception) as ctx:
                _execute_with_retry(
                    always_failing,
                    max_retries=2,
                    initial_delay=0.1,
                    backoff_factor=2.0,
                )

        self.assertIn("Persistent HTTP 429", str(ctx.exception))
        self.assertEqual(sleep_mock.call_count, 2)

    def test_non_transient_error_no_retry(self):
        attempts = 0

        def auth_failure():
            nonlocal attempts
            attempts += 1
            raise ValueError("Invalid API credentials provided")

        sleep_mock = MagicMock()
        with patch("time.sleep", sleep_mock):
            with self.assertRaises(ValueError):
                _execute_with_retry(auth_failure, max_retries=3, initial_delay=1.0)

        self.assertEqual(attempts, 1)
        sleep_mock.assert_not_called()

    def test_is_transient_error_string_status_codes_and_proxy_keywords(self):
        class StringStatusError(Exception):
            status_code = "429"
        self.assertTrue(_is_transient_error(StringStatusError()))

        class StringCode503Error(Exception):
            code = "503"
        self.assertTrue(_is_transient_error(StringCode503Error()))

        class ProxyOverloadedError(Exception):
            pass
        self.assertTrue(_is_transient_error(ProxyOverloadedError("Server overloaded, try again later")))
        self.assertTrue(_is_transient_error(Exception("upstream service unavailable (HTTP 502)")))
        self.assertTrue(_is_transient_error(Exception("rate_limit_exceeded")))

    def test_call_llm_openai_transient_retry_success(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client

        class RateLimitErr(Exception):
            status_code = 429

        mock_choice = MagicMock()
        mock_choice.message.content = "Retried successfully"
        mock_response = MagicMock(choices=[mock_choice])

        attempts = 0
        def side_effect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RateLimitErr("Rate limit exceeded 429")
            return mock_response

        mock_client.chat.completions.create.side_effect = side_effect

        Config.OPENAI_API_KEY = "test_key"
        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            with patch("time.sleep") as sleep_mock:
                out = call_llm(prompt="Retry test", provider="openai", max_retries=2, initial_delay=0.1)

        self.assertEqual(out, "Retried successfully")
        self.assertEqual(attempts, 2)
        sleep_mock.assert_called_once_with(0.1)

    def test_call_llm_gemini_transient_retry_success(self):
        mock_genai = MagicMock()
        mock_gemini_client = MagicMock()
        mock_genai.Client.return_value = mock_gemini_client

        class GeminiResourceExhausted(Exception):
            code = 429
            message = "Resource has been exhausted"

        attempts = 0
        def side_effect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise GeminiResourceExhausted("429 Quota Exceeded")
            resp = MagicMock()
            resp.text = "Gemini retried successfully"
            return resp

        mock_gemini_client.models.generate_content.side_effect = side_effect

        modules_patch = {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }
        with patch.dict(sys.modules, modules_patch):
            with patch("time.sleep") as sleep_mock:
                out = call_llm(
                    prompt="Retry test",
                    provider="gemini",
                    api_key="test_key",
                    max_retries=2,
                    initial_delay=0.1,
                )

        self.assertEqual(out, "Gemini retried successfully")
        self.assertEqual(attempts, 2)
        sleep_mock.assert_called_once_with(0.1)

    def test_execute_with_retry_negative_clamping(self):
        call_count = 0
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timeout")
            return "recovered"

        with patch("time.sleep"):
            # max_retries=-1 should clamp to 0 and raise without retrying
            with self.assertRaises(TimeoutError):
                _execute_with_retry(fn, max_retries=-1, initial_delay=-5.0)
        self.assertEqual(call_count, 1)

    def test_is_transient_error_response_status_code(self):
        class ResponseStatusError(Exception):
            response = MagicMock(status_code=429)

        self.assertTrue(_is_transient_error(ResponseStatusError()))

    def test_is_transient_error_non_transient_client_error_with_number_in_message(self):
        class BadRequestError(Exception):
            status_code = 400

        # Should NOT be transient even if message contains 502 or 429
        self.assertFalse(_is_transient_error(BadRequestError("Invalid parameter at line 502")))
        self.assertFalse(_is_transient_error(BadRequestError("User 42910 not found")))

    def test_is_transient_error_avoids_matching_port_numbers(self):
        # Port 5020 or 5000 should not be recognized as transient HTTP status 502/500
        self.assertFalse(_is_transient_error(Exception("Failed to bind to port 5020: address in use")))

    def test_execute_with_retry_string_inputs(self):
        attempts = 0
        def fn():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("timeout")
            return "ok"

        with patch("time.sleep"):
            out = _execute_with_retry(fn, max_retries="2", initial_delay="0.1", backoff_factor="1.5")
        self.assertEqual(out, "ok")
        self.assertEqual(attempts, 2)


class TestDynamicMockEngine(unittest.TestCase):
    """Test dynamic mock engine for archetypes, repair loops, and BenchmarkEvaluator."""

    def test_archetype_new_feature(self):
        # Spec generation
        spec = _mock_llm_response(
            prompt="Target Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Category Request: NEW_FEATURE",
            system_prompt="Feature 2 - SWE-bench Issue Specification Generator\nLOADED BY: src/spec_generator.py",
        )
        self.assertIn("Business Requirements", spec)
        self.assertIn("validate_state", spec)
        self.assertIn("apply_voucher", spec)

        # Diff generation
        diff = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification (instruction.md):\n# Feature: State Integrity & Feature Validation\nReturn ONLY the unified git diff patch.",
            system_prompt="Feature 3 - Ground-Truth Reference Solution Coder\nOUTPUT TARGET: solution/solution.diff",
        )
        self.assertIn("validate_state", diff)
        self.assertIn("apply_voucher", diff)
        self.assertIn("--- a/", diff)

        # Test generation
        tests = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification:\n# Feature: State Integrity & Feature Validation\nReturn ONLY valid Python pytest code.",
            system_prompt="Feature 4 - Hidden Acceptance Test Synthesizer\nOUTPUT TARGET: tests/test_feature_patch.py",
        )
        self.assertIn("test_feature_execution", tests)
        self.assertIn("validate_state", tests)
        self.assertIn("apply_voucher", tests)

    def test_archetype_validation_edge_cases(self):
        spec = _mock_llm_response(
            prompt="Target Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Category Request: VALIDATION_EDGE_CASES",
            system_prompt="Feature 2 - SWE-bench Issue Specification Generator",
        )
        self.assertIn("Defensive Validation", spec)
        self.assertIn("validate_input", spec)
        self.assertIn("sanitize_payload", spec)

        diff = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification:\n# Feature: Defensive Validation & Boundary Protection\nReturn ONLY the unified git diff patch.",
            system_prompt="Feature 3 - Ground-Truth Reference Solution Coder\nOUTPUT TARGET: solution/solution.diff",
        )
        self.assertIn("validate_input", diff)
        self.assertIn("sanitize_payload", diff)

        tests = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification:\n# Feature: Defensive Validation & Boundary Protection\nReturn ONLY valid Python pytest code.",
            system_prompt="Feature 4 - Hidden Acceptance Test Synthesizer\nOUTPUT TARGET: tests/test_feature_patch.py",
        )
        self.assertIn("validate_input", tests)
        self.assertIn("sanitize_payload", tests)

    def test_archetype_state_bug_fix(self):
        spec = _mock_llm_response(
            prompt="Target Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Category Request: STATE_BUG_FIX",
            system_prompt="Feature 2 - SWE-bench Issue Specification Generator",
        )
        self.assertIn("State Integrity & Mutation Bug Fix", spec)
        self.assertIn("get_safe_state", spec)
        self.assertIn("update_state", spec)

        diff = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification:\n# Feature: State Integrity & Mutation Bug Fix\nReturn ONLY the unified git diff patch.",
            system_prompt="Feature 3 - Ground-Truth Reference Solution Coder\nOUTPUT TARGET: solution/solution.diff",
        )
        self.assertIn("get_safe_state", diff)
        self.assertIn("update_state", diff)

        tests = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification:\n# Feature: State Integrity & Mutation Bug Fix\nReturn ONLY valid Python pytest code.",
            system_prompt="Feature 4 - Hidden Acceptance Test Synthesizer\nOUTPUT TARGET: tests/test_feature_patch.py",
        )
        self.assertIn("get_safe_state", tests)
        self.assertIn("update_state", tests)

    def test_archetype_performance_refactor(self):
        spec = _mock_llm_response(
            prompt="Target Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Category Request: PERFORMANCE_REFACTOR",
            system_prompt="Feature 2 - SWE-bench Issue Specification Generator",
        )
        self.assertIn("Performance Refactor", spec)
        self.assertIn("build_lookup_index", spec)
        self.assertIn("fast_lookup", spec)

        diff = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification:\n# Feature: Performance Refactor & Algorithmic Optimization\nReturn ONLY the unified git diff patch.",
            system_prompt="Feature 3 - Ground-Truth Reference Solution Coder\nOUTPUT TARGET: solution/solution.diff",
        )
        self.assertIn("build_lookup_index", diff)
        self.assertIn("fast_lookup", diff)

        tests = _mock_llm_response(
            prompt="Codebase Context:\n- class PaymentProcessor (in service.py):\nTask Specification:\n# Feature: Performance Refactor & Algorithmic Optimization\nReturn ONLY valid Python pytest code.",
            system_prompt="Feature 4 - Hidden Acceptance Test Synthesizer\nOUTPUT TARGET: tests/test_feature_patch.py",
        )
        self.assertIn("build_lookup_index", tests)
        self.assertIn("fast_lookup", tests)

    def test_self_healing_repair_diff(self):
        repair_prompt = """Codebase Context:
- class MethodSignature (in analyzer.py):
Task Specification:
# Feature: State Integrity & Feature Validation
Previous Solution Diff:
--- a/analyzer.py
+++ b/analyzer.py
@@ -10,6 +10,18 @@
The above solution failed during sandbox verification with the following error:
Patch failed to apply cleanly.
Please analyze the failure and provide a corrected unified git diff that resolves the issue and passes all tests.
Return ONLY the corrected unified git diff patch.
"""
        repaired_diff = _mock_llm_response(
            prompt=repair_prompt,
            system_prompt="Feature 3 - Ground-Truth Reference Solution Coder\nOUTPUT TARGET: solution/solution.diff",
        )
        self.assertIn("--- a/", repaired_diff)
        self.assertIn("validate_state", repaired_diff)
        self.assertTrue(repaired_diff.endswith("\n"))

    def test_self_healing_repair_tests(self):
        repair_prompt = """Codebase Context:
- class MethodSignature (in analyzer.py):
Task Specification:
# Feature: State Integrity & Feature Validation
Previous Test File:
import broken_syntax
The test execution failed with the following error:
ImportError: No module named broken_syntax
Please provide a corrected pytest test file. Ensure imports match the codebase accurately.
Return ONLY valid Python pytest code.
"""
        repaired_tests = _mock_llm_response(
            prompt=repair_prompt,
            system_prompt="Feature 4 - Hidden Acceptance Test Synthesizer\nOUTPUT TARGET: tests/test_feature_patch.py",
        )
        self.assertIn("import unittest", repaired_tests)
        self.assertIn("TestFeatureValidation", repaired_tests)
        self.assertIn("validate_state", repaired_tests)

    def test_benchmark_evaluator_prompt_handling(self):
        eval_template = """# FEATURE: Feature 8 - Frontier AI Benchmark Evaluator
# LOADED BY: src/benchmark_evaluator.py
You are an autonomous AI software engineer participating in a SWE-bench evaluation benchmark.
You are given a software specification describing a new feature, bug fix, or defensive validation requirement.
YOUR OBJECTIVE: Output ONLY a unified git diff against the codebase.

Task Specification (instruction.md):
{spec_header}
The `MethodSignature` in `analyzer.py` needs changes.
"""
        cases = [
            ("# Feature: State Integrity & Feature Validation", "validate_state"),
            ("# Feature: Defensive Validation & Boundary Protection", "validate_input"),
            ("# Feature: State Integrity & Mutation Bug Fix", "get_safe_state"),
            ("# Feature: Performance Refactor & Algorithmic Optimization", "build_lookup_index"),
        ]
        for spec_header, expected_method in cases:
            prompt = eval_template.format(spec_header=spec_header)
            diff = _mock_llm_response(prompt=prompt, system_prompt="")
            self.assertIn("--- a/", diff)
            self.assertIn(expected_method, diff, f"BenchmarkEvaluator failed to output {expected_method} for {spec_header}")

    def test_mock_category_case_insensitivity_and_archetype_kwarg(self):
        # Case insensitivity with category
        spec = _mock_llm_response("Test prompt", category="STATE_BUG_FIX")
        self.assertIn("State Integrity & Mutation Bug Fix", spec)

        # Archetype kwarg alias
        spec2 = _mock_llm_response("Test prompt", archetype="performance_refactor")
        self.assertIn("Performance Refactor", spec2)

    def test_mock_explicit_target_file_and_class_kwargs(self):
        spec = _mock_llm_response("Test prompt", target_file="custom_store.py", target_class="CustomStore")
        self.assertIn("custom_store.py", spec)
        self.assertIn("CustomStore", spec)

    def test_mock_repair_diff_and_repair_tests_actions(self):
        # Test action kwarg
        diff = _mock_llm_response("Test prompt", action="repair_diff")
        self.assertIn("--- a/", diff)

        test = _mock_llm_response("Test prompt", action="repair_tests")
        self.assertIn("import unittest", test)

        # Test boolean flag
        diff2 = _mock_llm_response("Test prompt", repair_diff=True)
        self.assertIn("--- a/", diff2)

        test2 = _mock_llm_response("Test prompt", repair_tests=True)
        self.assertIn("import unittest", test2)

    def test_call_llm_none_prompt_and_system_prompt(self):
        # Ensure None prompt and None system_prompt do not crash with AttributeError
        res = call_llm(prompt=None, system_prompt=None, provider="mock")
        self.assertIsInstance(res, str)
        self.assertTrue(len(res) > 0)

    def test_call_llm_openai_system_instruction_and_max_output_tokens(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client
        mock_choice = MagicMock()
        mock_choice.message.content = "Filtered kwargs response"
        mock_client.chat.completions.create.return_value.choices = [mock_choice]

        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(
                prompt="Test prompt",
                provider="openai",
                api_key="valid_key",
                system_instruction="You are a helpful reviewer.",
                max_output_tokens=500,
                config=MagicMock(),
                category="new_feature",
                target_file="sample.py",
                initial_delay=0.0,
            )

        self.assertEqual(out, "Filtered kwargs response")
        create_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(create_kwargs["messages"][0], {"role": "system", "content": "You are a helpful reviewer."})
        self.assertEqual(create_kwargs["messages"][1], {"role": "user", "content": "Test prompt"})
        self.assertEqual(create_kwargs["max_tokens"], 500)
        self.assertNotIn("system_instruction", create_kwargs)
        self.assertNotIn("max_output_tokens", create_kwargs)
        self.assertNotIn("config", create_kwargs)
        self.assertNotIn("category", create_kwargs)
        self.assertNotIn("target_file", create_kwargs)

    def test_is_transient_error_status_attribute(self):
        class StatusIntError(Exception):
            status = 429
        self.assertTrue(_is_transient_error(StatusIntError("Too Many Requests")))

        class Status400Error(Exception):
            status = 400
        self.assertFalse(_is_transient_error(Status400Error("Bad Request")))

        class StatusStringError(Exception):
            status = "RESOURCE_EXHAUSTED"
        self.assertTrue(_is_transient_error(StatusStringError("Exhausted quota")))

    def test_is_transient_error_deadline_exceeded(self):
        class DeadlineExceeded(Exception):
            pass
        self.assertTrue(_is_transient_error(DeadlineExceeded("gRPC deadline exceeded")))
        self.assertTrue(_is_transient_error(Exception("504 DEADLINE_EXCEEDED")))

    def test_call_llm_numeric_string_temperature_and_max_tokens(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="Numeric parsed"))
        ]

        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(
                prompt="Test prompt",
                provider="openai",
                api_key="key",
                temperature="0.7",
                max_tokens="1024",
                initial_delay=0.0,
            )
        self.assertEqual(out, "Numeric parsed")
        create_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(create_kwargs["temperature"], 0.7)
        self.assertEqual(create_kwargs["max_tokens"], 1024)

    def test_openai_dict_response_handling(self):
        mock_openai_module = MagicMock()
        mock_client = MagicMock()
        mock_openai_module.OpenAI.return_value = mock_client

        # Mock proxy returning dict choices
        dict_choice = {"message": {"content": "dict response content"}}
        mock_response = MagicMock()
        mock_response.choices = [dict_choice]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict(sys.modules, {"openai": mock_openai_module}):
            out = call_llm(prompt="Test dict", provider="openai", api_key="key")
        self.assertEqual(out, "dict response content")

    def test_is_transient_error_mock_grpc_error_and_details(self):
        class MockGrpcError(Exception):
            def code(self):
                class Status:
                    name = "RESOURCE_EXHAUSTED"
                    value = (8, "resource exhausted")
                return Status()
            def details(self):
                return "Quota exceeded for project 123"

        err = MockGrpcError("gRPC call failed")
        self.assertTrue(_is_transient_error(err))

    def test_is_transient_error_standard_class_names(self):
        class ResourceExhausted(Exception):
            pass
        class ServiceUnavailableError(Exception):
            pass
        class TooManyRequests(Exception):
            pass

        self.assertTrue(_is_transient_error(ResourceExhausted()))
        self.assertTrue(_is_transient_error(ServiceUnavailableError()))
        self.assertTrue(_is_transient_error(TooManyRequests()))

    def test_is_transient_error_http_529_and_5xx_ranges(self):
        class Status529Error(Exception):
            status_code = 529
        self.assertTrue(_is_transient_error(Status529Error()))

        class Status521Error(Exception):
            status_code = 521
        self.assertTrue(_is_transient_error(Status521Error()))

        # 501 Not Implemented is non-transient
        class Status501Error(Exception):
            status_code = 501
        self.assertFalse(_is_transient_error(Status501Error()))

    def test_openai_raw_dict_response(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = {
            "choices": [{"message": {"content": "Hello from dict response"}}]
        }

        with patch.dict(sys.modules, {"openai": mock_openai}):
            res = call_llm("test prompt", provider="openai", api_key="dummy")
        self.assertEqual(res, "Hello from dict response")

    def test_openai_content_blocks_list(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_choice = MagicMock()
        mock_choice.message.content = [{"type": "text", "text": "Block content"}]
        mock_choice.message.text = None
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        with patch.dict(sys.modules, {"openai": mock_openai}):
            res = call_llm("test prompt", provider="openai", api_key="dummy")
        self.assertEqual(res, "Block content")

    def test_gemini_multiple_parts_extraction(self):
        mock_genai = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        class DummyResponse:
            @property
            def text(self):
                raise ValueError("Thinking model has multiple parts")
            @property
            def candidates(self):
                part0 = MagicMock(text="")
                part1 = MagicMock(text="Real content from part 1")
                mock_content = MagicMock(parts=[part0, part1])
                return [MagicMock(content=mock_content)]

        mock_client.models.generate_content.return_value = DummyResponse()

        modules_patch = {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }
        with patch.dict(sys.modules, modules_patch):
            res = call_llm("test prompt", provider="gemini", api_key="dummy")
        self.assertEqual(res, "Real content from part 1")

    def test_model_and_provider_whitespace_fallback(self):
        # Provider whitespace fallback
        res = call_llm("prompt", provider="   ")
        self.assertIsInstance(res, str)

        # Model whitespace fallback to Config.MODEL_NAME
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content="ok"))])

        with patch.dict(sys.modules, {"openai": mock_openai}):
            call_llm("test prompt", provider="openai", model="   ", api_key="dummy")
            called_model = mock_client.chat.completions.create.call_args[1]["model"]
        self.assertEqual(called_model, Config.MODEL_NAME)

    def test_find_target_file_on_disk_none(self):
        self.assertIsNone(_find_target_file_on_disk(None))
        self.assertIsNone(_find_target_file_on_disk(""))
        self.assertIsNone(_find_target_file_on_disk("   "))

    def test_generate_mock_test_and_diff_none_and_whitespace_target_file_compiles(self):
        # Mock test generation with None/whitespace should produce valid, compilable Python
        test_code_none = _generate_mock_test(None, None, "new_feature")
        compile(test_code_none, "<test_none>", "exec")

        test_code_ws = _generate_mock_test("   ", "   ", "validation_edge_cases")
        compile(test_code_ws, "<test_ws>", "exec")

        # Mock diff generation with None/whitespace should produce valid unified diff headers
        diff_none = _generate_mock_diff(None, None, "new_feature")
        self.assertIn("--- a/service.py", diff_none)

        diff_ws = _generate_mock_diff("   ", "   ", "new_feature")
        self.assertIn("--- a/service.py", diff_ws)

    def test_generate_mock_spec_none_and_whitespace(self):
        spec_none = _generate_mock_spec(None, None, "new_feature")
        self.assertNotIn("The `None` in `None`", spec_none)
        self.assertIn("PaymentProcessor", spec_none)
        self.assertIn("service.py", spec_none)

    def test_is_transient_error_callable_status_string(self):
        class CallableStatusError(Exception):
            def status(self):
                return "RESOURCE_EXHAUSTED"

        err = CallableStatusError("API call failed")
        self.assertTrue(_is_transient_error(err))

    def test_call_llm_provider_aliases(self):
        # Test google-genai alias
        mock_genai = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value.text = "genai alias works"
        modules_patch = {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }
        with patch.dict(sys.modules, modules_patch):
            out_genai = call_llm(prompt="Hello", provider="google-genai", api_key="dummy")
        self.assertEqual(out_genai, "genai alias works")

        # Test openai-compatible alias
        mock_openai = MagicMock()
        mock_oai_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_oai_client
        mock_oai_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="compatible alias works"))
        ]
        with patch.dict(sys.modules, {"openai": mock_openai}):
            out_compat = call_llm(prompt="Hello", provider="openai-compatible", api_key="dummy")
        self.assertEqual(out_compat, "compatible alias works")

    def test_mock_archetype_taxonomy_names_and_prompt_request(self):
        # Human-readable taxonomy names from VariationController.TAXONOMY
        cases = [
            ("Defensive Validation & Error Handling", "validation_edge_cases", "validate_input"),
            ("Concurrency & State Mutation Bug Fix", "state_bug_fix", "get_safe_state"),
            ("Algorithmic Refactor & Optimization", "performance_refactor", "build_lookup_index"),
            ("New Functional Feature", "new_feature", "validate_state"),
        ]
        for name, expected_cat, expected_method in cases:
            # 1. Prompt with Task Category Request: <Taxonomy Name>
            prompt = f"Target Codebase Context:\n- class PaymentProcessor:\nTask Category Request: {name}"
            spec = _mock_llm_response(prompt=prompt, system_prompt="Feature 2 - SWE-bench Issue Specification Generator")
            self.assertIn(expected_method, spec, f"Failed for {name} in spec")

            # 2. Category kwarg matching taxonomy name
            diff = _mock_llm_response("Generate diff", system_prompt="Feature 3 - Ground-Truth Reference Solution Coder", category=name)
            self.assertIn(expected_method, diff, f"Failed for {name} in diff")

            # 3. Test generation matching taxonomy name
            test_code = _mock_llm_response("Generate test", system_prompt="Feature 4 - Hidden Acceptance Test Synthesizer", category=name)
            self.assertIn(expected_method, test_code, f"Failed for {name} in tests")

    def test_is_transient_error_chained_exceptions(self):
        # Wrapped TimeoutError in cause
        try:
            raise TimeoutError("connection timed out")
        except Exception as inner:
            outer = RuntimeError("LLM gateway failure")
            outer.__cause__ = inner

        self.assertTrue(_is_transient_error(outer))

        # Wrapped RateLimit in context
        class RateLimitErr(Exception):
            status_code = 429

        try:
            raise RateLimitErr("Too many requests")
        except Exception as inner2:
            outer2 = Exception("Service unavailable wrapper")
            outer2.__context__ = inner2

        self.assertTrue(_is_transient_error(outer2))

    def test_gemini_dict_response_parsing(self):
        mock_genai = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Gemini text from dict response", "thought": False}
                        ]
                    }
                }
            ]
        }

        modules_patch = {
            "google": MagicMock(genai=mock_genai),
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }
        with patch.dict(sys.modules, modules_patch):
            out = call_llm(prompt="Hello", provider="gemini", api_key="dummy_key")
        self.assertEqual(out, "Gemini text from dict response")

    def test_openai_reasoning_models_max_completion_tokens(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_choice = MagicMock()
        mock_choice.message.content = "o1 reasoning response"
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        with patch.dict(sys.modules, {"openai": mock_openai}):
            out = call_llm(
                prompt="Reason about code",
                provider="openai",
                model="o1-mini",
                temperature=0.7,
                max_tokens=4096,
                api_key="dummy_key",
            )

        self.assertEqual(out, "o1 reasoning response")
        create_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(create_kwargs["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", create_kwargs)
        self.assertNotIn("temperature", create_kwargs)

    def test_openai_provider_endpoint_defaults(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_choice = MagicMock()
        mock_choice.message.content = "Groq response"
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        # Test Groq default endpoint routing
        with patch.dict(sys.modules, {"openai": mock_openai}):
            with patch.dict(os.environ, {"GROQ_API_KEY": "groq_secret"}):
                res = call_llm("Groq fast prompt", provider="groq")
        self.assertEqual(res, "Groq response")
        mock_openai.OpenAI.assert_called_with(api_key="groq_secret", timeout=60.0, base_url="https://api.groq.com/openai/v1")

        # Test Ollama local default endpoint routing (no API key required)
        with patch.dict(sys.modules, {"openai": mock_openai}):
            res_ollama = call_llm("Ollama prompt", provider="ollama")
        self.assertEqual(res_ollama, "Groq response")
        mock_openai.OpenAI.assert_called_with(api_key="dummy_local_key", timeout=60.0, base_url="http://localhost:11434/v1")

    def test_mock_diff_absolute_path_handling(self):
        diff = _generate_mock_diff("/tmp/project/src/service.py", "PaymentProcessor", "new_feature")
        self.assertNotIn("--- a//", diff)
        self.assertIn("--- a/", diff)


if __name__ == "__main__":
    unittest.main()
