# Official Hack2Skill PPT Deck Structure (Owned by Yashwant)

This document maps our project directly to the mandatory Hack2skill submission presentation deck template.

---

### Slide 1: Title & Team Introduction
* **Project Name:** InnoData Automated Coding Task Factory
* **Track:** Track 1 - Coding Task Factory
* **Team Members:**
  * Shreya — Sandbox Verifier & SWE-bench Engine
  * Maximus — Task Ideation & Specification Generator
  * Manya — Solution Coder & Pytest Author
  * Yashwant — Meaningful Variation, Benchmarking & Presentation Lead
* **Repo Link:** https://github.com/shrey159/innodata-code-task-factory

---

### Slide 2: The Problem Statement (Why Existing Benchmarks Fail)
* **The Contamination Problem:** Frontier AI models are trained on public GitHub repos; old benchmarks test memorization rather than actual software engineering capability.
* **The Manual Bottleneck:** Human senior engineers take hours and thousands of dollars to create, code, and test a single SWE-bench task.
* **The Goal:** Build an automated "Factory" that programmatically ingests any codebase, formulates realistic tasks, writes reference solutions, and validates them without human intervention.

---

### Slide 3: Multi-Agent System Architecture
* Visual diagram of the 4-stage pipeline:
  1. *Codebase Scanner & Spec Agent* (`src/analyzer.py`, `src/spec_generator.py`)
  2. *Code & Test Synthesis Agent* (`src/coder_agent.py`, `src/test_agent.py`)
  3. *Sandbox Verifier & Packager* (`src/verifier.py`, `src/packager.py`)
  4. *Variation & Benchmarking Controller* (`src/variation_controller.py`, `src/benchmark_evaluator.py`)
* Strict enforcement of **"WHAT to build, never HOW"** in `instruction.md`.

---

### Slide 4: Mathematical Validation Matrix (25% Validity Evidence)
* Explain the 3-Step Verification Quality Gate:
  1. **Baseline:** Original repository + existing tests $\rightarrow$ **PASS** (Codebase is healthy)
  2. **Negative Check (Fail-to-Pass):** Original repo + new tests $\rightarrow$ **FAIL** (Proves tests are sound and not false positives)
  3. **Positive Check (Pass-to-Pass):** Repo + `solution.diff` + all tests $\rightarrow$ **PASS** (Proves reference solution is complete and has no regressions)
* Include a screenshot of the CLI output showing: `[1] PASS, [2] FAIL (Verified), [3] PASS`.

---

### Slide 5: Meaningful Variation Strategy (20% Variation Score)
* Show how our factory avoids trivial variations (e.g., button colors or string changes).
* Showcase our **4 Engineering Archetypes**:
  * Archetype 1: New Functional Feature (e.g. Voucher discount engine)
  * Archetype 2: Defensive Validation & Error Handling (Boundary checks, null guards)
  * Archetype 3: State Mutation & Concurrency Fixes (Collection references)
  * Archetype 4: Algorithmic Refactor & Optimization (Complexity reduction)
* Include a chart of our diversity score ($\ge 75\%$).

---

### Slide 6: Live Benchmarking Results (Frontier AI Evaluation)
* Display real benchmarking results using `src/benchmark_evaluator.py`:
  * Tasks evaluated: 5 diverse generated packages.
  * Model tested: e.g. Claude 3.5 Sonnet / GPT-4o.
  * Findings: Show where the AI succeeded vs. which edge cases it missed!

---

### Slide 7: Standardized Task Package Output (`task.toml`)
* Show the standardized SWE-bench package structure:
  ```text
  tasks/task_001_new_feature/
  ├── task.toml                  # Metadata & environment configuration
  ├── instruction.md             # Unbiased requirement specification
  ├── validation_report.json     # Verification audit trail
  ├── solution/solution.diff     # Ground-truth reference patch
  └── tests/test_feature_patch.py # Hidden evaluation test suite
  ```

---

### Slide 8: Future Roadmap & Impact
* Universal language support (JavaScript, TypeScript, Go, Rust).
* Self-healing feedback loop with Docker container isolation.
* Impact: Enterprise continuous benchmarking pipeline for AI engineering teams.
