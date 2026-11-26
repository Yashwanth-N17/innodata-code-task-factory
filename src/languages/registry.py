"""
Language Registry & Auto-Detection Engine.
Dispatches repositories to the best matching LanguageAdapter and allows
1-line registration for any future language (Go, Rust, Java, etc.).
"""

from typing import Dict, List, Optional, Tuple, Type

from .base import BaseLanguageAdapter
from .python import PythonAdapter
from .typescript import TypeScriptAdapter
from .cpp import CppAdapter
from .go import GoAdapter
from .rust import RustAdapter
from .java import JavaAdapter


class LanguageRegistry:
    """Central registry of pluggable language adapters."""

    _ADAPTERS: Dict[str, Type[BaseLanguageAdapter]] = {
        "python": PythonAdapter,
        "typescript": TypeScriptAdapter,
        "cpp": CppAdapter,
        "go": GoAdapter,
        "rust": RustAdapter,
        "java": JavaAdapter,
    }

    _ALIASES: Dict[str, str] = {
        "py": "python",
        "python3": "python",
        "ts": "typescript",
        "js": "typescript",
        "javascript": "typescript",
        "node": "typescript",
        "c++": "cpp",
        "cxx": "cpp",
        "c": "cpp",
        "golang": "go",
        "rs": "rust",
        "jdk": "java",
    }

    @classmethod
    def register(cls, adapter_cls: Type[BaseLanguageAdapter], aliases: Optional[List[str]] = None) -> None:
        """Register a new BaseLanguageAdapter subclass to support another language."""
        lang_id = adapter_cls.language_id.lower()
        cls._ADAPTERS[lang_id] = adapter_cls
        for alias in (aliases or []):
            cls._ALIASES[alias.lower()] = lang_id

    @classmethod
    def list_languages(cls) -> List[str]:
        """Return sorted list of registered canonical language identifiers."""
        return sorted(cls._ADAPTERS.keys())

    @classmethod
    def detect(cls, repo_path: str) -> Tuple[BaseLanguageAdapter, Dict[str, int]]:
        """Score every registered adapter against repo_path and return the best match."""
        scores: Dict[str, int] = {}
        for lang_id, adapter_cls in cls._ADAPTERS.items():
            try:
                scores[lang_id] = adapter_cls.detect_score(repo_path)
            except Exception:
                scores[lang_id] = 0

        if not scores or max(scores.values()) == 0:
            return PythonAdapter(), scores

        best_lang = max(scores.keys(), key=lambda k: (scores[k], k == "python"))
        return cls._ADAPTERS[best_lang](), scores

    @classmethod
    def get_adapter(
        cls,
        repo_path: str,
        language: Optional[str] = None,
    ) -> BaseLanguageAdapter:
        """Return an adapter instance for `language` (or auto-detect when None/'auto')."""
        if not language or str(language).strip().lower() in ("", "auto"):
            adapter, _ = cls.detect(repo_path)
            return adapter

        norm = str(language).strip().lower()
        canonical = cls._ALIASES.get(norm, norm)
        if canonical not in cls._ADAPTERS:
            supported = ", ".join(cls.list_languages())
            raise ValueError(f"Unsupported language '{language}'. Registered languages: {supported}")
        return cls._ADAPTERS[canonical]()
