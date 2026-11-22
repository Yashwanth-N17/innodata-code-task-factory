# 3-Minute Demo Video Script (Owned by Yashwant)

**Total Duration:** 3 to 4 Minutes  
**Video Goal:** Prove to InnoData judges that our factory is functional, automated, repeatable, and mathematically verified.

---

### [0:00 – 0:45] Intro & Problem Statement
* **Visual on Screen:** Title Slide / GitHub Repository.
* **Speaker (Yashwant):**  
  > "Hello everyone! Welcome to our demo for the InnoData AI Hackathon Track 1: Coding Task Factory.
  > Today, frontier AI coding agents are evaluated on static, contaminated datasets where models often pass tests simply through memorization. Furthermore, creating new SWE-bench tasks manually takes hours of senior engineering time.
  > Our solution is an end-to-end, multi-agent automated factory that ingests any software repository and programmatically produces realistic, solvable, and reliably verifiable coding benchmark task packages."

---

### [0:45 – 1:45] Architecture & Live Factory Demo
* **Visual on Screen:** Split screen: Terminal on the left, VS Code on the right.
* **Action:** Run the command in terminal:
  ```bash
  python run_factory.py --repo sample_repo --mock --count 1
  ```
* **Speaker (Yashwant):**  
  > "Let's see the factory in action. We run `python run_factory.py`.
  > Notice what happens under the hood:
  > First, our Codebase Scanner parses the AST to inspect classes, base hierarchies, and full method signatures with parameter annotations and public/private separation.
  > Next, our Specification Agent writes `instruction.md` following strict SWE-bench rules—specifying business requirements, I/O schemas, and at least 3 concrete edge cases, with our automated leakage auditor guaranteeing zero line numbers or private internal variables are revealed.
  > Then, our Code and Test Synthesis agents generate the reference `solution.diff` and the hidden pytest assertions.
  > Finally, our Sandbox Verifier runs the rigorous 3-step matrix:
  > Step 1: Baseline tests PASS.
  > Step 2: Negative check FAILS on the unpatched repo—proving the test is sound.
  > Step 3: Positive check PASSES once the patch is applied."

---

### [1:45 – 2:30] Inspecting the Generated Task Package
* **Visual on Screen:** Open the generated folder in `tasks/`.
* **Action:** Open `task.toml`, `instruction.md`, and `validation_report.json`.
* **Speaker (Yashwant):**  
  > "Here is our packaged task. Notice the clean structure:
  > 1. `instruction.md` acts as the unbiased prompt for an AI agent.
  > 2. `task.toml` provides automated test commands and environment configs.
  > 3. `validation_report.json` contains the mathematical audit trail proving all 3 verification gates passed."

---

### [2:30 – 3:15] Meaningful Variation & Benchmarking
* **Visual on Screen:** Slide showing the 4 Variation Archetypes and Benchmark Evaluator results.
* **Speaker (Yashwant):**  
  > "To ensure our tasks are non-trivial and achieve high marks in Meaningful Variation, our Variation Controller enforces 4 distinct archetypes: New Features, Defensive Error Validation, State Mutation Fixes, and Algorithmic Refactoring.
  > Using our Benchmark Evaluator, we tested frontier models on these packages. We can observe real failure modes on edge cases, proving these tasks provide genuine signal on model capabilities."

---

### [3:15 – 3:30] Conclusion
* **Visual on Screen:** GitHub repository link & Team slide.
* **Speaker (Yashwant):**  
  > "Our factory is 100% automated, repeatable, and repository-agnostic. Thank you InnoData and Hack2skill for this incredible challenge!"
