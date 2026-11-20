# Team Contribution Guide

Welcome team! This repository houses our **Automated Coding Task Factory** for the InnoData AI Hackathon (Track 1).

To avoid Git merge conflicts and work smoothly across our 4-person team, we follow this structured workflow.

---

## 1. Team Role Assignments

| Role / Member | Focus Area | Primary Directories / Files |
| :--- | :--- | :--- |
| **Shreya** | Task Ideation & Spec Generator | `src/analyzer.py`, `src/spec_generator.py`, `prompts/spec_system_prompt.txt` |
| **Manya** | Solution Coder & Test Author | `src/coder_agent.py`, `src/test_agent.py`, `prompts/coder_prompt.txt`, `prompts/test_prompt.txt` |
| **Maximus** | Sandbox & SWE-bench Verifier | `src/verifier.py`, `src/packager.py`, verification runner |
| **Yashwant** | Variation, Benchmark & PPT Lead | `src/variation_controller.py`, `src/benchmark_evaluator.py`, official PPT deck & video |

---

## 2. Git Branching Strategy

Never push directly to `main`! Always work on your designated feature branch.

### Designated Team Feature Branches:
* **Shreya:** `feature/shreya-spec-generator`
* **Manya:** `feature/manya-code-test-synth`
* **Maximus:** `feature/maximus-sandbox-verifier`
* **Yashwant:** `feature/yashwant-variation-benchmark`

### Step-by-Step Git Workflow:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/shrey159/innodata-code-task-factory.git
   cd innodata-code-task-factory
   ```

2. **Switch to your assigned feature branch:**
   ```bash
   # For Shreya:
   git checkout feature/shreya-spec-generator

   # For Manya:
   git checkout feature/manya-code-test-synth

   # For Maximus:
   git checkout feature/maximus-sandbox-verifier

   # For Yashwant:
   git checkout feature/yashwant-variation-benchmark
   ```

3. **Make your changes, test them, and stage:**
   ```bash
   git add src/spec_generator.py
   git commit -m "feat(spec): add AST symbol parser and requirement prompt"
   ```

4. **Keep your branch updated with main:**
   ```bash
   git fetch origin
   git rebase origin/main
   ```

5. **Push your branch to GitHub:**
   ```bash
   git push -u origin feature/member-1-spec-generator
   ```

6. **Create a Pull Request (PR) on GitHub:**
   * Open GitHub, click "Compare & pull request".
   * Ask at least one teammate to review and click "Merge".

---

## 3. Local Environment Setup

1. **Create a Python Virtual Environment:**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Mac/Linux:
   source venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   ```bash
   cp .env.example .env
   # Edit .env and paste your GEMINI_API_KEY or OPENAI_API_KEY
   ```

4. **Run Mock Test (no API key required):**
   ```bash
   python run_factory.py --repo sample_repo --mock
   ```
