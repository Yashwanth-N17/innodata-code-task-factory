"""
Test-suite isolation for provider configuration loaded from the local environment.
"""

import os
from unittest.mock import patch

import pytest

from src.config import Config


@pytest.fixture(autouse=True)
def isolate_provider_configuration():
    """Keep tests independent of developer .env values and API credentials."""
    original = {
        "provider": Config.LLM_PROVIDER,
        "model": Config.MODEL_NAME,
        "gemini_key": Config.GEMINI_API_KEY,
        "openai_key": Config.OPENAI_API_KEY,
        "anthropic_key": Config.ANTHROPIC_API_KEY,
        "groq_key": Config.GROQ_API_KEY,
    }

    Config.LLM_PROVIDER = "mock"
    Config.GEMINI_API_KEY = None
    Config.OPENAI_API_KEY = None
    Config.ANTHROPIC_API_KEY = None
    Config.GROQ_API_KEY = None

    with patch.dict(
        os.environ,
        {
            "LLM_PROVIDER": "mock",
            "GEMINI_API_KEY": "",
            "GOOGLE_API_KEY": "",
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "GROQ_API_KEY": "",
        },
    ):
        yield

    Config.LLM_PROVIDER = original["provider"]
    Config.MODEL_NAME = original["model"]
    Config.GEMINI_API_KEY = original["gemini_key"]
    Config.OPENAI_API_KEY = original["openai_key"]
    Config.ANTHROPIC_API_KEY = original["anthropic_key"]
    Config.GROQ_API_KEY = original["groq_key"]
