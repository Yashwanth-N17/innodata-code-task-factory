# InnoData Automated Coding Task Factory (Track 1)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![SWE-bench Compliant](https://img.shields.io/badge/benchmark-SWE--bench-orange.svg)](https://www.swebench.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An end-to-end, reusable, multi-agent pipeline that ingests any software repository and programmatically generates **realistic, solvable, and reliably verifiable software engineering tasks** to benchmark frontier AI coding agents (Claude Code, Devin, GPT-4o).

Built for the **InnoData AI Hackathon** (powered by Hack2skill).

---

## 🏗️ Architecture Overview

The factory is designed with a strict separation of concerns across a 4-stage pipeline:

```
                  ┌──────────────────────────────────────────────┐
                  │          Target Software Repository          │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                             [Step 1: Codebase Scanner]
                             AST Parsing & Symbol Crawler
                                         │
                                         ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                       MULTI-AGENT GENERATION LOOP                       │
    │                                                                         │
    │   [Specification Agent]             Generates instruction.md (WHAT)     │
    │             │                                                           │
    │             ▼                                                           │
    │   [Code & Test Synthesis Agent]     Generates solution.diff & Pytest    │
    │             │                                                           │
    │             ▼                                                           │
    │   [Sandbox Verifier & Packager]     SWE-bench 3-Step Matrix & toml      │
    └────────────────────────────────────┬────────────────────────────────────┘
                                         │
                                         ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                    EVALUATION & BENCHMARKING ENGINE                     │
    │                                                                         │
    │   [Variation & Benchmark Engine]    Task Diversity & Frontier AI Eval   │
    └─────────────────────────────────────────────────────────────────────────┘
```

---

## 🎯 Core Engineering Principles

### 1. AST Semantic Scanning & SWE-bench Specification: "WHAT" to Build, Never "HOW"
* **Codebase Semantic Scanner (`src/analyzer.py`):**
  * Statically crawls the codebase using Python `ast` without executing arbitrary code.
  * Discovers classes, class inheritance (`bases`), public method signatures (parameters, defaults, return types), docstrings, and imports while strictly separating internal/private methods (`_` prefix).
  * Accurately distinguishes test suites (`tests/`, `test_*.py`) from source files containing 'test' (e.g., `src/test_agent.py`).
* **SWE-bench Issue Specification (`instruction.md`):**
  * Formatted as a real-world software issue ticket: Business Requirements, Input/Output Schemas, Acceptance Criteria, and at least 3 concrete Edge Cases.
  * **Automated Multi-Pattern Leakage Auditor (`validate_leakage` & `sanitize_spec`):** Automatically detects and redacts explicit line numbers (`line \d+`, `file.py:\d+`, `L\d+`) and private variable hints (`self._...`), guaranteeing zero implementation leakage.

### 2. The 3-Step Verification Matrix (Validity Evidence - 25%)
Every generated task must pass our automated quality gate before packaging:

| Step | Codebase State | Test Executed | Expected Result | What It Proves |
| :--- | :--- | :--- | :---: | :--- |
| **1. Baseline** | Original untouched repository | Existing test suite | **PASS** | Original codebase is healthy and working. |
| **2. Negative Check** | Original untouched repository | **New feature test** | **FAIL** | Proves the test is a valid `FAIL_TO_PASS` check and not a false positive. |
| **3. Positive Check** | Repo + `solution.diff` applied | All tests (existing + new) | **PASS** | Proves the reference code genuinely solves the task without regressions (`PASS_TO_PASS`). |

### 3. Meaningful Variation Taxonomy (Variation - 20%)
To prevent repetitive, trivial tasks (e.g. changing text or button colors), our `VariationController` steers generation across 4 distinct engineering archetypes:
1. **New Functional Feature:** Introduces a new API route or class capability.
2. **Defensive Validation & Error Handling:** Hardens functions against out-of-bounds, null, or adversarial inputs.
3. **State Mutation & Concurrency Bug Fix:** Fixes edge-case data corruption in shared collections.
4. **Algorithmic Refactor & Optimization:** Improves runtime complexity while preserving full backward compatibility.

### 4. Polyglot Multi-Language & Real-World Repository Support
The factory natively supports polyglot codebases beyond synthetic toys. Tested and proven end-to-end on real, production open-source repositories:
- 🐍 **Python (`pytest`):** Validated on [`pallets/click`](https://github.com/pallets/click) with `src/`-layout packaging and rich test fixtures.
- ⚡ **TypeScript / JavaScript (`vitest` / Node `test` runner):** Validated on [`colinhacks/zod`](https://github.com/colinhacks/zod) with monorepo workspace package resolution, `--experimental-transform-types` enum support, and ambient dependency fallback.
- ⚙️ **C++ (`cmake` & `ctest`):** Validated on [`open-source-parsers/jsoncpp`](https://github.com/open-source-parsers/jsoncpp) with header-guard-scoped symbol injection avoiding multi-translation-unit redefinition errors.
- ☕ **Java (`maven` / `gradle`), 🦀 Rust (`cargo`), 🐹 Go (`go test`):** Supported via pluggable language adapter strategies (`src/languages/`).

### 5. Multi-Model Benchmark Evaluator & Live Dashboard
- **Frontier AI Benchmark Harness (`run_benchmark.py`):** Automatically evaluates models (Claude 3.5 Sonnet, GPT-4o, Gemini 2.0 Flash) on generated task packages to report Pass@1 metrics and generate markdown/JSON leaderboards.
- **Interactive Web Dashboard (`app.py`):** Streamlit interface for live factory runs, task package inspection, validation evidence audit logs, and benchmark leaderboard viewing.

---

## 📦 Task Package Structure

Verified tasks are automatically packaged into standardized directories ready for SWE-bench evaluation:

```text
tasks/task_1795146198_001_new_feature/
├── task.toml                  # Repo URL, commit hash, environment & test config
├── instruction.md             # Unbiased feature specification (given to AI agent)
├── validation_report.json     # Automated audit trail of the 3-step matrix
├── solution/
│   └── solution.diff          # Ground-truth reference implementation patch
└── tests/
    └── test_feature_patch.py  # Hidden pytest / vitest / ctest evaluation suite
```

---

## 🚀 Quickstart Guide

### 1. Clone & Set Up Virtual Environment
```bash
git clone https://github.com/Yashwanth-N17/innodata-code-task-factory.git
cd innodata-code-task-factory

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Set your LLM_PROVIDER ('gemini', 'openai', 'anthropic', or 'mock') and API Key in .env
```

### 3. Run the Factory
```bash
# Offline demo run on any local or remote repository (zero API credits required):
python run_factory.py --repo /path/to/repo --mock --count 1

# Production run using live Gemini, Claude, or OpenAI:
python run_factory.py --repo https://github.com/pallets/click --count 3

# Real-world polyglot repository verification:
python run_factory.py --repo https://github.com/colinhacks/zod --mock --count 1
python run_factory.py --repo https://github.com/open-source-parsers/jsoncpp --mock --count 1
```

### 4. Run the Benchmark Evaluator
```bash
# Benchmark AI models against generated tasks:
python run_benchmark.py --repo /path/to/repo --tasks-dir tasks/ --models gemini-2.0-flash,gpt-4o,claude-3-5-sonnet --mock
```

### 5. Launch the Visual Dashboard
```bash
streamlit run app.py
```

---

## 🤝 Team & Contribution

This project is developed collaboratively for the InnoData AI Hackathon 2026.
For internal team module assignments, engineering ownership, and Pull Request guidelines, please refer to [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📜 License
MIT License. Developed for the InnoData AI Hackathon 2026.
