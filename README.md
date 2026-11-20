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
    │   [Member 1: Spec Agent]            Generates instruction.md (WHAT)     │
    │             │                                                           │
    │             ▼                                                           │
    │   [Member 2: Coder Agent]           Generates solution.diff             │
    │   [Member 2: Test Agent]            Generates pytest test_patch.py      │
    │             │                                                           │
    │             ▼                                                           │
    │   [Member 3: Verifier]              SWE-bench 3-Step Validation Matrix  │
    │             │                                                           │
    │             ▼                                                           │
    │   [Member 3: Packager]              Standardized task.toml Package      │
    └────────────────────────────────────┬────────────────────────────────────┘
                                         │
                                         ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                    EVALUATION & BENCHMARKING ENGINE                     │
    │                                                                         │
    │   [Member 4: Variation Controller]  Multi-Dimensional Task Diversity    │
    │   [Member 4: Benchmark Evaluator]   Frontier AI Scoring & Analytics     │
    └─────────────────────────────────────────────────────────────────────────┘
```

---

## 🎯 Core Engineering Principles

### 1. Specification: "WHAT" to Build, Never "HOW" (`instruction.md`)
Following SWE-bench standards, `instruction.md` is formatted as a real-world software issue ticket. It defines:
* Business requirements and user goals.
* Precise input/output schemas and public interface contracts.
* Minimum 3 edge cases and defensive exception guarantees.
* **Strict Prohibition:** Never reveals line numbers, code snippets, or implementation steps.

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
    └── test_feature_patch.py  # Hidden pytest evaluation suite
```

---

## 🚀 Quickstart Guide

### 1. Clone & Set Up Virtual Environment
```bash
git clone <YOUR_REPO_URL>
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
# Set your LLM_PROVIDER ('gemini', 'openai', or 'mock') and API Key in .env
```

### 3. Run the Factory
```bash
# Offline demo run (zero API credits required):
python run_factory.py --repo sample_repo --mock --count 2

# Production run using live Gemini or OpenAI APIs:
python run_factory.py --repo path/to/your/repo --count 5
```

---

## 👥 Team Roles & Responsibilities

| Role | Focus | Primary Files Owned |
| :--- | :--- | :--- |
| **Member 1** | Task Ideation & Specification | `src/analyzer.py`, `src/spec_generator.py`, `prompts/spec_system_prompt.txt` |
| **Member 2** | Code & Test Synthesis | `src/coder_agent.py`, `src/test_agent.py`, `prompts/coder_prompt.txt`, `prompts/test_prompt.txt` |
| **Member 3** | Sandbox Verifier & Packager | `src/verifier.py`, `src/packager.py`, verification runner |
| **Member 4** | Variation, Benchmark & PPT Lead | `src/variation_controller.py`, `src/benchmark_evaluator.py`, Presentation Deck |

See [CONTRIBUTING.md](CONTRIBUTING.md) for Git branching rules and team workflow.

---

## 📜 License
MIT License. Developed for the InnoData AI Hackathon 2026.
