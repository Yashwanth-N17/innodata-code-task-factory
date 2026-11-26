"""Pluggable polyglot language adapters for the Task Factory."""

from .base import BaseLanguageAdapter, DEFAULT_IGNORED_DIRS, parse_junit_xml
from .python import PythonAdapter, MethodSignature
from .typescript import TypeScriptAdapter
from .cpp import CppAdapter
from .registry import LanguageRegistry

__all__ = [
    "BaseLanguageAdapter",
    "DEFAULT_IGNORED_DIRS",
    "parse_junit_xml",
    "PythonAdapter",
    "MethodSignature",
    "TypeScriptAdapter",
    "CppAdapter",
    "LanguageRegistry",
]
