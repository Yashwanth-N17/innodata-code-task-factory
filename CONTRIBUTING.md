# Developer & Feature Contribution Guide

Welcome to the **Automated Coding Task Factory** for the InnoData AI Hackathon (Track 1).

To ensure anyone can contribute and build features **concurrently without blocking or waiting on each other**, each component is designed as an independent feature with strict input/output contracts, mock fallbacks, and isolated test commands.

---

## 1. Feature Architecture Overview

```
                ┌──────────────────────────────────────────────┐
                │          Target Software Repository          │
                └──────────────────────┬───────────────────────┘
                                       │
                                       ▼
                     [Feature 1: Codebase Semantic Scanner]
                     AST Symbol Parsing, Imports & Classes
                                       │
                                       ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                 PARALLEL MULTI-AGENT GENERATION PIPELINE                │
  │                                                                         │
  │   [Feature 2: Spec Generator]      --> instruction.md (WHAT, not HOW)   │
  │                   │                                                     │
  │                   ▼                                                     │
  │   [Feature 3 & 4: Code & Tests]    --> solution.diff & test_patch.py    │
  │                   │                                                     │
  │                   ▼                                                     │
  │   [Feature 5 & 6: Sandbox & Pack]  --> 3-Step Matrix & task.toml        │
  └────────────────────────────────────┬────────────────────────────────────┘
                                       │
                                       ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                    EVALUATION & BENCHMARKING ENGINE                     │
  │                                                                         │
  │   [Feature 7 & 8: Variation & Eval] --> 4 Archetypes & Frontier Eval    │
  └─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Feature Playbooks & Decoupled Execution Guide

### 🛠️ Feature 1: Codebase Semantic Scanner & AST Symbol Extractor
* **Module:** `src/analyzer.py`
* **Purpose:** Crawl a target repository and parse Python AST trees to extract classes, public method signatures, docstrings, imports, and existing test suites without executing arbitrary code.
* **Input Received:** Target repository directory path.
* **Output Produced:** Structured dictionary of symbols + formatted context summary string.
* **How to Test Independently:**
  ```bash
  python -c "from src.analyzer import CodebaseAnalyzer; print(CodebaseAnalyzer('src').get_context_prompt())"
  ```

---

### 🛠️ Feature 2: SWE-bench Issue Specification & Leakage Auditor
* **Modules:** `src/spec_generator.py`, `prompts/spec_system_prompt.txt`
* **Purpose:** Generate realistic GitHub-issue-style problem specifications defining **WHAT** needs to be built (business requirements, I/O schemas, $\ge 3$ edge cases), with automated auditing to strip line numbers and implementation exposure.
* **Input Received:** Codebase context summary + category archetype.
* **Output Produced:** Validated `instruction.md` Markdown string.
* **Crucial Rule:** Describe WHAT must be built. Never reveal HOW (no line numbers, no private internal variables).
* **How to Test Independently:**
  ```bash
  python -c "from src.spec_generator import SpecGenerator; gen = SpecGenerator(); print(gen.generate('class Cart: pass', 'new_feature'))"
  ```

---

### 🛠️ Feature 3: Reference Solution Coder & Self-Repair Agent
* **Modules:** `src/coder_agent.py`, `prompts/coder_prompt.txt`
* **Purpose:** Generate the reference solution formatted strictly as a unified Git diff (`solution.diff`), with a self-repair feedback loop (`repair_diff()`) to fix rejected patches or test failures.
* **Input Received:** Codebase context string + `instruction.md`.
* **Output Produced:** Valid unified Git diff patch string (`solution.diff`).
* **How to Test Independently:**
  ```bash
  python -c "from src.coder_agent import CoderAgent; coder = CoderAgent(); print(coder.generate_diff('class Cart: pass', 'Add voucher discount'))"
  ```

---

### 🛠️ Feature 4: Hidden Acceptance Test Synthesizer
* **Modules:** `src/test_agent.py`, `prompts/test_prompt.txt`
* **Purpose:** Synthesize hidden `pytest` test suites that enforce the fail-to-pass guarantee (must fail on the unmodified repo, must pass once the reference solution is applied). Includes a repair loop (`repair_tests()`).
* **Input Received:** Codebase context string + `instruction.md`.
* **Output Produced:** Valid Python pytest file content (`test_patch.py`).
* **Crucial Rule:** Tests MUST FAIL before applying the patch and MUST PASS after applying the patch.
* **How to Test Independently:**
  ```bash
  python -c "from src.test_agent import TestAgent; tester = TestAgent(); print(tester.generate_tests('class Cart: pass', 'Add voucher discount'))"
  ```

---

### 🛠️ Feature 5: Sandbox Verifier & Multi-Tier Patch Engine
* **Module:** `src/verifier.py`
* **Purpose:** Execute the automated **3-Step SWE-bench Verification Matrix** in an isolated temporary clone:
  1. **Baseline Check:** Untouched repo + existing tests $\rightarrow$ **PASS**
  2. **Negative Check (Fail-to-Pass):** Untouched repo + new tests $\rightarrow$ **FAIL** (validates test is sound)
  3. **Positive Check (Pass-to-Pass):** Repo + `solution.diff` + all tests $\rightarrow$ **PASS** (solvability proof)
  Includes multi-tier patch application fallback (standard git apply $\rightarrow$ fuzz `-C1` $\rightarrow$ pure-Python applier).
* **Input Received:** Target repository path + `solution.diff` + `test_patch.py`.
* **Output Produced:** `VerificationResult` object (step statuses + stdout/stderr execution logs).
* **How to Test Independently:**
  ```bash
  python -m unittest tests.test_factory
  ```

---

### 🛠️ Feature 6: SWE-bench Task Packager
* **Module:** `src/packager.py`
* **Purpose:** Serialize and format verified tasks into standardized directories under `tasks/<task_id>/` with `task.toml`, `instruction.md`, `solution/solution.diff`, `tests/test_feature_patch.py`, and `validation_report.json`.
* **Input Received:** Task metadata dictionary, instruction, diff, test code, and verification result.
* **Output Produced:** Formatted directory on disk.
* **How to Test Independently:**
  ```bash
  python -c "from src.packager import TaskPackager; p = TaskPackager('tasks'); print('Packager initialized successfully!')"
  ```

---

### 🛠️ Feature 7: Meaningful Variation & Archetype Controller
* **Module:** `src/variation_controller.py`
* **Purpose:** Steer task generation across 4 distinct engineering archetypes to avoid trivial tasks (e.g. changing strings or button colors):
  1. *New Functional Feature* (API or method addition)
  2. *Defensive Validation & Error Handling* (null guards, bounds checks)
  3. *State Mutation & Concurrency Bug Fix* (collection references)
  4. *Algorithmic Refactor & Optimization* (complexity improvements)
* **Input Received:** Requested task batch size ($N$).
* **Output Produced:** Balanced category list + diversity score distribution report.
* **How to Test Independently:**
  ```bash
  python -c "from src.variation_controller import VariationController; vc = VariationController(); print(vc.select_category(4)); print(vc.get_variation_report())"
  ```

---

### 🛠️ Feature 8: Frontier AI Benchmark Evaluation Harness
* **Modules:** `src/benchmark_evaluator.py`, `prompts/evaluator_prompt.txt`
* **Purpose:** Prompt external frontier models (e.g. Claude 3.5 Sonnet, GPT-4o) with generated `instruction.md` files, capture their proposed patches, and evaluate their pass rate using the Sandbox Verifier.
* **Input Received:** Packaged task directory + target model identifier.
* **Output Produced:** Benchmark score report (solve rate, pass/fail audit).
* **How to Test Independently:**
  ```bash
  python -c "from src.benchmark_evaluator import BenchmarkEvaluator; print('Benchmark Evaluator initialized!')"
  ```

---

### 🛠️ Feature 9: Multi-Provider LLM & Dynamic Mock Service
* **Module:** `src/config.py`
* **Purpose:** Unified LLM wrapper supporting Google Gemini (`google-genai`), OpenAI (`openai`), and a dynamic mock engine that adapts to any codebase for rapid local offline testing without API keys.
* **Input Received:** Prompt string + optional system prompt.
* **Output Produced:** Cleaned model completion string.
* **How to Test Independently:**
  ```bash
  python -c "from src.config import call_llm; print(call_llm('Hello world!')[:50])"
  ```

---

## 3. End-to-End Pipeline Execution

Once any feature is updated, run the full pipeline to verify integration:

```bash
# 1. Run unit test suite
python -m unittest discover tests

# 2. Run automated task factory against any codebase (mock mode)
python run_factory.py --repo src --mock --count 2

# 3. Run automated task factory with live LLM (requires .env configuration)
python run_factory.py --repo src --count 2
```

---

## 4. Git Contribution Workflow

1. Create a feature branch:
   ```bash
   git checkout -b feature/<feature-name>
   ```
2. Make your edits and run independent tests.
3. Commit and push:
   ```bash
   git commit -m "feat(feature-name): implement functionality"
   git push origin feature/<feature-name>
   ```
4. Open a Pull Request on GitHub.
