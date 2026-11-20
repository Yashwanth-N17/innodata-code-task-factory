# Prompts Directory Guide

This directory contains the system prompts that guide our LLM agents. Each prompt is owned by a specific team member and loaded by a specific Python script in `src/`.

---

## 📋 Prompt Ownership & Usage Table

| Prompt File | Owner | Loaded By | Purpose |
| :--- | :--- | :--- | :--- |
| **[`spec_system_prompt.txt`](spec_system_prompt.txt)** | **Maximus** | `src/spec_generator.py` | Instructs LLM to generate `instruction.md` (*WHAT to build, never HOW*). |
| **[`coder_prompt.txt`](coder_prompt.txt)** | **Manya** | `src/coder_agent.py` | Instructs LLM to generate the working reference implementation as a unified Git diff (`solution.diff`). |
| **[`test_prompt.txt`](test_prompt.txt)** | **Manya** | `src/test_agent.py` | Instructs LLM to generate the hidden `pytest` test suite (`test_feature_patch.py`) that fails before and passes after. |
| **[`evaluator_prompt.txt`](evaluator_prompt.txt)** | **Yashwant** | `src/benchmark_evaluator.py` | Acts as the system prompt when evaluating external AI models (e.g. Claude, GPT-4o) against our generated tasks. |

---

## 💡 How to Modify Prompts
Any team member can tune and improve their prompt by editing the respective `.txt` file directly. **No Python code needs to be modified!**
