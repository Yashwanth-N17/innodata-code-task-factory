# Team Contribution Guide

Welcome team! This repository houses our **Automated Coding Task Factory** for the InnoData AI Hackathon (Track 1).

To avoid Git merge conflicts and work smoothly across our 4-person team, we follow this structured workflow.

---

## 1. Team Role Assignments

| Role | Member Name | Focus Area | Primary Directories / Files |
| :--- | :--- | :--- | :--- |
| **Member 1** | TBD | Task Ideation & Spec Generator | `src/analyzer.py`, `src/spec_generator.py`, `prompts/spec_system_prompt.txt` |
| **Member 2** | TBD | Solution Coder & Test Author | `src/coder_agent.py`, `src/test_agent.py`, `prompts/coder_prompt.txt`, `prompts/test_prompt.txt` |
| **Member 3** | TBD | Sandbox & SWE-bench Verifier | `src/verifier.py`, `src/packager.py`, verification runner |
| **Member 4** | TBD | Variation, Benchmark & PPT Lead | `src/variation_controller.py`, `src/benchmark_evaluator.py`, official PPT deck & video |

---

## 2. Git Branching Strategy

Never push directly to `main`! Always work on your designated feature branch.

### Branch Names:
* Member 1: `feature/member-1-spec-generator`
* Member 2: `feature/member-2-code-test-synth`
* Member 3: `feature/member-3-sandbox-verifier`
* Member 4: `feature/member-4-variation-benchmark`

### Step-by-Step Git Workflow:

1. **Clone the repository:**
   ```bash
   git clone <YOUR_GITHUB_REPO_URL>
   cd innodata-code-task-factory
   ```

2. **Create and switch to your feature branch:**
   ```bash
   git checkout -b feature/member-1-spec-generator
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
