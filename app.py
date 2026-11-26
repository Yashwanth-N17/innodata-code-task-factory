"""
Interactive Streamlit Dashboard for the InnoData Automated Coding Task Factory.
Launch via: `streamlit run app.py`
"""

import json
import os
import subprocess
import sys

import streamlit as st

from src.benchmark_evaluator import BatchBenchmarkRunner
from src.exporter import export_swebench_jsonl

st.set_page_config(page_title="InnoData Coding Task Factory", layout="wide")
st.title("InnoData Automated Coding Task Factory (Polyglot Track 1)")

tab_run, tab_tasks, tab_bench = st.tabs([
    "1. Generate Tasks",
    "2. Task Package Inspector",
    "3. Frontier AI Leaderboard",
])

with tab_run:
    st.subheader("Run Polyglot Task Generation Pipeline")
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        repo_input = st.text_input(
            "Target Repository (Local path or GitHub URL)",
            value="https://github.com/pallets/click",
        )
    with col2:
        lang_choice = st.selectbox("Language", ["auto", "python", "typescript", "cpp", "go", "rust", "java"])
    with col3:
        task_count = st.number_input("Task Count", min_value=1, max_value=12, value=2)

    c_opt1, c_opt2, c_opt3 = st.columns(3)
    with c_opt1:
        mock_mode = st.checkbox("Mock Mode (Offline)", value=True)
    with c_opt2:
        sandbox_mode = st.selectbox("Sandbox", ["local", "docker"])
    with c_opt3:
        setup_env = st.checkbox("Bootstrap Dependencies (--setup-env)", value=False)

    if st.button("Launch Factory", type="primary"):
        cmd = [
            sys.executable, "run_factory.py",
            "--repo", repo_input,
            "--language", lang_choice,
            "--count", str(task_count),
            "--sandbox", sandbox_mode,
        ]
        if mock_mode:
            cmd.append("--mock")
        if setup_env:
            cmd.append("--setup-env")
        with st.spinner("Running multi-agent scan, synthesis, and 3-step verification..."):
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        st.code(proc.stdout + ("\n" + proc.stderr if proc.stderr else ""), language="text")

with tab_tasks:
    st.subheader("Verified SWE-bench Task Packages")
    tasks_root = st.text_input("Tasks Directory", value="tasks")
    if st.button("Export Verified Tasks to SWE-bench JSONL"):
        summary = export_swebench_jsonl(tasks_dir=tasks_root, output_path=os.path.join(tasks_root, "swebench_dataset.jsonl"))
        st.success(f"Exported {summary['exported_count']}/{summary['total_scanned']} verified tasks to {summary['output_path']}")

    if os.path.isdir(tasks_root):
        task_dirs = sorted([
            d for d in os.listdir(tasks_root)
            if os.path.isdir(os.path.join(tasks_root, d))
            and os.path.isfile(os.path.join(tasks_root, d, "instruction.md"))
        ])
        if not task_dirs:
            st.info("No packaged tasks found yet. Generate a task in Tab 1.")
        else:
            selected = st.selectbox("Select Task Package", task_dirs)
            tdir = os.path.join(tasks_root, selected)
            report_path = os.path.join(tdir, "validation_report.json")
            report = {}
            if os.path.isfile(report_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    report = json.load(f)

            checks = report.get("checks") or {}
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Status", report.get("status", "UNKNOWN"))
            m2.metric("Step 1 Baseline", "PASS" if checks.get("step1_baseline_pass") else "FAIL")
            m3.metric("Step 2 Negative (Fail-to-Pass)", "FAIL (OK)" if checks.get("step2_negative_fail") else "INVALID")
            m4.metric("Step 3 Positive", "PASS" if checks.get("step3_positive_pass") else "FAIL")

            p1, p2 = st.columns(2)
            with p1:
                st.markdown("#### Specification (`instruction.md`)")
                with open(os.path.join(tdir, "instruction.md"), "r", encoding="utf-8") as f:
                    st.markdown(f.read())
            with p2:
                st.markdown("#### Reference Patch (`solution/solution.diff`)")
                diff_p = os.path.join(tdir, "solution", "solution.diff")
                if os.path.isfile(diff_p):
                    with open(diff_p, "r", encoding="utf-8") as f:
                        st.code(f.read(), language="diff")

                st.markdown("#### Task Configuration (`task.toml`)")
                toml_p = os.path.join(tdir, "task.toml")
                if os.path.isfile(toml_p):
                    with open(toml_p, "r", encoding="utf-8") as f:
                        st.code(f.read(), language="toml")

with tab_bench:
    st.subheader("Frontier AI Model Benchmark Leaderboard")
    lb_path = st.text_input("Leaderboard JSON Path", value="benchmark_results/leaderboard.json")
    if os.path.isfile(lb_path):
        with open(lb_path, "r", encoding="utf-8") as f:
            lb_report = json.load(f)
        st.markdown(BatchBenchmarkRunner.render_markdown_leaderboard(lb_report))
        st.json(lb_report.get("leaderboard", []))
    else:
        st.info("No leaderboard report found yet. Run `python run_benchmark.py --repo <repo> --models gemini-2.0-flash,claude-3-5-sonnet,gpt-4o`.")
