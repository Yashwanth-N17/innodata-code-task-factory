# Future Zod End-to-End Verification Procedure

**Prerequisite:** This procedure must be run in an environment where `nub install` succeeds (Nub 0.8.3 tarball download transport working). Do NOT execute until Nub dependency installation is resolved.

**Branch:** `fix/zod-e2e-verification`

---

## Step 0 — Verify branch and repository state

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory
git branch --show-current
git status --short
git log --oneline -3
```

Expected: branch is `fix/zod-e2e-verification`, working tree clean except intended changes.

---

## Step 1 — Verify Node and Nub versions

```cmd
node --version
nub --version
npm --version
```

Expected: Node v24.12.0, Nub v0.8.3.

---

## Step 2 — Verify the declared package manager

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory\repos\colinhacks__zod
nub config get packageManager
```

Expected: `nub@0.8.3` (from `package.json`).

---

## Step 3 — Install dependencies with declared frozen-lockfile command

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory\repos\colinhacks__zod
nub install --frozen-lockfile
```

Expected: exit code 0, `node_modules` directory created, `node_modules/.bin/vitest` exists.

Verify:
```cmd
if exist node_modules\.bin\vitest (echo "vitest found") else (echo "vitest MISSING")
if exist node_modules\.bin\vitest.cmd (echo "vitest.cmd found") else (echo "vitest.cmd MISSING")
```

---

## Step 4 — Confirm local Vitest resolution

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory\repos\colinhacks__zod
node_modules\.bin\vitest --version
```

Expected: vitest v4.x output (not a global install).

Confirm NO global vitest is being used:
```cmd
where vitest
```
Should only show the local `node_modules\.bin\vitest` (or nothing if not on PATH).

---

## Step 5 — Run Zod's upstream baseline tests

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory\repos\colinhacks__zod
nub exec --node vitest run
```

Expected: exit code 0, all upstream tests pass (205+ tests across packages/zod/src/v3/tests/ and packages/zod/src/v4/).

Record: test count, duration, exit code.

---

## Step 6 — Run the factory against actual Zod repository

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory
python run_factory.py --repo "C:\Users\LENOVO\m21\innodata-code-task-factory\repos\colinhacks__zod" --language typescript --mock --count 1 --sandbox local --timeout 120 --setup-env
```

Expected:
- Repository analysis detects TypeScript/Vitest
- Bootstrap detects `nub@0.8.3` and runs `nub install --frozen-lockfile`
- Sandbox preparation finds local vitest in `node_modules/.bin`
- Generated test placed in `packages/zod/tests/test_feature_patch.test.ts` (workspace-aware)
- Step 1 Baseline: PASS
- Step 2 Negative: FAIL (genuine new-test failure)
- Step 3 Positive: PASS (patch applied, all tests pass)
- Task packaged successfully
- Exit code 0

---

## Step 7 — Verify generated test discovery

Inspect the generated task package:
```cmd
dir /s /b tasks\task_*\*
```

Expected files:
- `instruction.md`
- `solution/solution.diff`
- `packages/zod/tests/test_feature_patch.test.ts` (workspace-aware placement, NOT `tests/test_feature_patch.test.ts`)
- `task.toml`
- `validation_report.json`

Open `validation_report.json` and confirm:
```json
{
  "status": "VERIFIED",
  "checks": {
    "step1_baseline_pass": true,
    "step2_negative_fail": true,
    "step3_positive_pass": true,
    "patch_applied_cleanly": true
  },
  "evaluation": {
    "fail_to_pass": ["..."],
    "pass_to_pass": ["..."]
  }
}
```

---

## Step 8 — Demonstrate genuine fail-before/pass-after behavior

From `validation_report.json`:
- `fail_to_pass` must contain the generated test ID (e.g. `packages/zod/tests/test_feature_patch.test.ts::test_name`)
- `pass_to_pass` must contain existing upstream test IDs
- `step2_negative_fail` must be `true` (new test failed before patch)
- `step3_positive_pass` must be `true` (all tests pass after patch)

---

## Step 9 — Verify pass-to-pass regression behavior

From `validation_report.json`:
- `pass_to_pass` test IDs correspond to existing upstream tests (not `test_feature_patch`)
- Those tests passed in Step 1 baseline AND Step 3 after patch

---

## Step 10 — Inspect packaged task and reproduce verification

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory
for /d %d in (tasks\task_*) do @echo %d
```

Pick the latest task directory and inspect:
```cmd
type tasks\task_latest\validation_report.json
type tasks\task_latest\task.toml
type tasks\task_latest\instruction.md
```

To independently reproduce verification, copy the task package to a clean location and run the patch against the Zod sandbox.

---

## Step 11 — Regression validation

```cmd
cd /d C:\Users\LENOVO\m21\innodata-code-task-factory
python -m pytest tests/ -q
python -m pytest tests/test_polyglot.py -k "click or jsoncpp" -q
```

Expected:
- Full suite: 0 failed
- Click tests: passed
- JsonCpp: skipped (no cmake/ctest on Windows — not a regression)

---

## Zod Status Labels

| Condition | Label |
|-----------|-------|
| Nub install fails (current environment) | BLOCKED |
| Nub install succeeds, all 3 steps pass | PASSED |
| Nub install succeeds, any step fails | FAILED |

**Current status: BLOCKED** — Nub 0.8.3 tarball download transport fails on this machine ("HTTP response-body decoding errors" on binary packages like `@esbuild/win32-x64`). Direct `curl` to registry.npmjs.org works, confirming the issue is Nub-specific, not network-related.
