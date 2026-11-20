# Team Contribution Guide

Welcome team! This repository houses our **Automated Coding Task Factory** for the InnoData AI Hackathon (Track 1).

To ensure everyone can work smoothly without blocking each other or causing Git conflicts, each member owns a dedicated module with clear inputs, outputs, and independent test commands.

---

## 1. Quick Team Summary

| Member | Module Owned | Focus Area | Workload & Difficulty |
| :--- | :--- | :--- | :--- |
| **Shreya** | Sandbox Verifier & Engine | `src/verifier.py`, `src/packager.py` | ⭐⭐⭐ Heavy (25% Validity Score) |
| **Maximus** | Spec & Ideation Generator | `src/analyzer.py`, `src/spec_generator.py` | ⭐⭐ Moderate (Fits busy schedule) |
| **Manya** | Code & Pytest Synthesis | `src/coder_agent.py`, `src/test_agent.py` | ⭐⭐⭐ Moderate-Heavy |
| **Yashwant** | Variation, Benchmark & PPT | `src/variation_controller.py`, `benchmark` | ⭐⭐ Strategic (20% Variation + Pitch) |

---

## 2. Detailed Member Playbooks (What to Code & How to Test)

### 👤 Maximus: Task Ideation & Specification Generator
* **Your Goal:** Scan the target repository and prompt an LLM to generate `instruction.md`.
* **Primary Files:** 
  * `src/analyzer.py` (AST symbol parser)
  * `src/spec_generator.py` (LLM prompt caller)
  * `prompts/spec_system_prompt.txt` (System prompt template)
* **Input You Receive:** Raw target repository directory path.
* **Output You Produce:** Clean markdown string for `instruction.md`.
* **Crucial SWE-bench Rule:** 
  > Specify **WHAT** needs to be built (business goals, inputs/outputs, edge cases). **NEVER give away HOW** (no implementation code, no line numbers).
* **How to Test Your Code Independently:**
  ```bash
  python -c "from src.analyzer import CodebaseAnalyzer; from src.spec_generator import SpecGenerator; ctx = CodebaseAnalyzer('src').get_context_prompt(); print(SpecGenerator().generate(ctx))"
  ```

---

### 👤 Manya: Solution Coder & Test Author
* **Your Goal:** Given the repo and Maximus's `instruction.md`, prompt an LLM to write the reference solution code and hidden pytest assertions.
* **Primary Files:**
  * `src/coder_agent.py` (Generates the reference implementation)
  * `src/test_agent.py` (Generates the pytest test suite)
  * `prompts/coder_prompt.txt` & `prompts/test_prompt.txt`
* **Input You Receive:** Target codebase context + `instruction.md` from Maximus.
* **Output You Produce:**
  1. `solution.diff`: A unified Git patch (`diff --git a/...`) that applies cleanly via `git apply`.
  2. `test_patch.py`: Pytest test functions testing the acceptance criteria and edge cases.
* **Crucial SWE-bench Rule:**
  > Your test cases **MUST FAIL** on the unpatched codebase (because the feature doesn't exist yet) and **MUST PASS** once `solution.diff` is applied!
* **How to Test Your Code Independently:**
  ```bash
  python -c "from src.coder_agent import CoderAgent; from src.test_agent import TestAgent; print('Coder & Tester agents imported successfully!')"
  ```

---

### 👤 Shreya: Sandbox Verifier & SWE-bench Engine
* **Your Goal:** Build the automated quality gate that runs the 3-step test matrix in an isolated sandbox and packages verified tasks into `task.toml`.
* **Primary Files:**
  * `src/verifier.py` (Subprocess runner executing git and pytest in temp folders)
  * `src/packager.py` (Builds standardized task packages and writes `task.toml`)
* **Input You Receive:** `solution.diff` and `test_patch.py` from Manya.
* **Output You Produce:** Standardized folder under `tasks/<task_id>/` with `task.toml`, `instruction.md`, `solution/solution.diff`, `tests/`, and `validation_report.json`.
* **The 3-Step Verification Matrix (25% of Hackathon Score):**
  1. **Baseline Check:** Untouched repo + existing tests $\rightarrow$ **PASS**
  2. **Negative Check:** Untouched repo + new tests $\rightarrow$ **FAIL** (`FAIL_TO_PASS` verified)
  3. **Positive Check:** Repo + `solution.diff` + all tests $\rightarrow$ **PASS** (`PASS_TO_PASS` verified)
* **How to Test Your Code Independently:**
  ```bash
  python -m unittest discover tests
  ```

---

### 👤 Yashwant: Variation Controller, Benchmarking & Presentation Lead
* **Your Goal:** Prevent repetitive/trivial tasks (securing the 20% "Meaningful Variation" score), benchmark a real model, and lead official submission deliverables.
* **Primary Files:**
  * `src/variation_controller.py` (Enforces task diversity across 4 archetypes)
  * `src/benchmark_evaluator.py` (Evaluates external LLMs on generated tasks)
  * `presentation/` (Official Hack2skill PPT deck & 5-minute video demo)
* **The 4 Variation Archetypes:**
  1. *New Functional Feature* (API addition or new capability)
  2. *Defensive Validation & Error Handling* (Hardening edge cases, boundary checks)
  3. *State Mutation & Concurrency Bug Fix* (Fixing edge cases in collections)
  4. *Algorithmic Refactor & Optimization* (Complexity improvement)
* **Key Deliverables for Hack2skill (Due Dec 6):**
  * Official PPT presentation deck (using the mandatory Hack2skill template).
  * 3–5 minute video demo uploaded to YouTube/Drive.
* **How to Test Your Code Independently:**
  ```bash
  python -c "from src.variation_controller import VariationController; vc = VariationController(); cats = vc.select_category(4); print(vc.get_variation_report())"
  ```

---

## 3. Standard Pull Request (PR) Workflow

To contribute code without conflicts:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/shrey159/innodata-code-task-factory.git
   cd innodata-code-task-factory
   ```

2. **Create your working feature branch:**
   ```bash
   git checkout -b feature/my-update
   ```

3. **Make your changes & test locally:**
   ```bash
   # Run unit tests
   python -m unittest discover tests

   # Test factory against any target repository
   python run_factory.py --repo path/to/target/repo --mock --count 1
   ```

4. **Commit and push to GitHub:**
   ```bash
   git add .
   git commit -m "feat(module): description of changes"
   git push origin feature/my-update
   ```

5. **Open a Pull Request (PR):**
   * Go to [github.com/shrey159/innodata-code-task-factory](https://github.com/shrey159/innodata-code-task-factory).
   * Click **Compare & pull request** against `main`.
   * Have a teammate review and merge into `main`!

---

## 4. Local Environment & API Setup

1. **Create and activate virtual environment:**
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Linux/Mac:
   source venv/bin/activate
   ```

2. **Install requirements:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API Keys (for live LLM generation):**
   ```bash
   cp .env.example .env
   # Open .env and add your GEMINI_API_KEY or OPENAI_API_KEY
   ```
