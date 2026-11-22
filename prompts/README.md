# Prompts Directory Guide

This directory contains the system prompts that guide our LLM agents. Each prompt is mapped to a specific feature and loaded by a specific Python script in `src/`.

---

## 📋 Feature Prompt Mapping & Usage Table

| Prompt File | Feature Track | Loaded By | Purpose |
| :--- | :--- | :--- | :--- |
| **[`spec_system_prompt.txt`](spec_system_prompt.txt)** | **Feature 2: Spec Generator** | `src/spec_generator.py` | Instructs LLM to generate `instruction.md` (*WHAT to build, never HOW*) across 7 required sections: Task Title, Problem Description, Business Requirements, I/O Schema, Acceptance Criteria, Edge Cases ($\ge 3$), and Target Interface. |
| **[`coder_prompt.txt`](coder_prompt.txt)** | **Feature 3: Solution Coder** | `src/coder_agent.py` | Instructs LLM to generate the working reference implementation as a unified Git diff (`solution.diff`). |
| **[`test_prompt.txt`](test_prompt.txt)** | **Feature 4: Test Synthesizer** | `src/test_agent.py` | Instructs LLM to generate the hidden `pytest` test suite (`test_feature_patch.py`) that fails before and passes after. |
| **[`evaluator_prompt.txt`](evaluator_prompt.txt)** | **Feature 8: Benchmark Evaluator** | `src/benchmark_evaluator.py` | Acts as the system prompt when evaluating external AI models (e.g. Claude, GPT-4o) against our generated tasks. |

---

## 💡 How to Tune Prompts
Any developer can tune and improve their prompt by editing the respective `.txt` file directly. **No Python code needs to be modified!**
