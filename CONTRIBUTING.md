# Team Contribution Guide

Welcome team! This repository houses our **Automated Coding Task Factory** for the InnoData AI Hackathon (Track 1).

To avoid Git merge conflicts and work smoothly across our 4-person team, we follow this structured workflow.

---

## 1. Team Role Assignments

| Role / Member | Focus Area | Primary Directories / Files |
| :--- | :--- | :--- |
| **Shreya** | **Sandbox Verifier & SWE-bench Engine** | `src/verifier.py`, `src/packager.py`, verification runner |
| **Maximus** | Task Ideation & Spec Generator | `src/analyzer.py`, `src/spec_generator.py`, `prompts/spec_system_prompt.txt` |
| **Manya** | Solution Coder & Test Author | `src/coder_agent.py`, `src/test_agent.py`, `prompts/coder_prompt.txt`, `prompts/test_prompt.txt` |
| **Yashwant** | Variation, Benchmark & PPT Lead | `src/variation_controller.py`, `src/benchmark_evaluator.py`, official PPT deck & video |

---

## 2. Standard Pull Request (PR) Workflow

1. **Clone the repository:**
   ```bash
   git clone https://github.com/shrey159/innodata-code-task-factory.git
   cd innodata-code-task-factory
   ```

2. **Create a new branch for your task:**
   ```bash
   git checkout -b feature/my-update
   ```

3. **Make your changes, test them locally:**
   ```bash
   # Run the mock pipeline to verify
   python run_factory.py --repo sample_repo --mock --count 1

   # Run unit tests
   python -m unittest discover tests
   ```

4. **Commit and push to your branch:**
   ```bash
   git add .
   git commit -m "feat(module): description of changes"
   git push origin feature/my-update
   ```

5. **Open a Pull Request on GitHub:**
   * Go to [github.com/shrey159/innodata-code-task-factory](https://github.com/shrey159/innodata-code-task-factory).
   * Click **Compare & pull request** against `main`.
   * Have a team member review and merge.

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
