"""Run Claude Code (headless) on WALBench tasks and grade with the hidden grader.

    uv run python -m walbench.agent --arm read_only      # B: bug report + format spec, edit by reasoning
    uv run python -m walbench.agent --arm with_check     # C: + ./check (40 visible crash scenarios, shows a failure)
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .grader import grade
from .tasks import HIDDEN

ROOT = Path(__file__).resolve().parents[2]
FORMAT = (Path(__file__).parent / "engine.py").read_text().split('"""')[1]
SYSTEM = {"read_only": """You are fixing a bug in the crash-recovery code of a small write-ahead-logged key-value store.
The working directory has recovery.py (the code to fix), BUG_REPORT.md (what users saw) and FORMAT.md (the on-disk format
and the recovery contract). Find the bug and fix it in recovery.py with the smallest correct change.
Keep the function signature recover(dirpath) -> dict. When done, reply with one sentence describing the fix."""}
SYSTEM["with_check"] = SYSTEM["read_only"] + """
You can test your fix with ./check, which replays 40 simulated crashes against recovery.py and shows a failing case."""


def run_one(task_dir, arm, model, effort, root):
    d = Path(tempfile.mkdtemp(prefix=f"wal-{task_dir.name}-", dir=root))
    shutil.copy(task_dir / "recovery.py", d / "recovery.py")
    shutil.copy(task_dir / "BUG_REPORT.md", d / "BUG_REPORT.md")
    (d / "FORMAT.md").write_text("# On-disk format and recovery contract\n" + FORMAT)
    tools, allowed = "Read,Edit,Write", ["Read", "Edit", "Write"]
    if arm == "with_check":
        py = ROOT / ".venv" / "bin" / "python"
        (d / "check").write_text(f'#!/bin/sh\ncd "{ROOT}" && exec "{py}" -m walbench.grader "{d}/recovery.py" --n 40 --seed 1 --show 1\n')
        (d / "check").chmod(0o755)
        tools += ",Bash"
        allowed += ["Bash(./check)", "Bash(./check:*)"]
    cmd = ["claude", "-p", "Fix the bug described in BUG_REPORT.md.", "--output-format", "json", "--model", model,
           "--effort", effort, "--system-prompt", SYSTEM[arm], "--tools", tools, "--allowedTools", ",".join(allowed),
           "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--max-budget-usd", "2"]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")}
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=900, env=env)
        out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
    except subprocess.TimeoutExpired:
        out = {}
    g = grade(str(d / "recovery.py"), **HIDDEN, show=0)
    u = out.get("usage") or {}
    return {"task": task_dir.name, "arm": arm, "pass": g["pass"], "hidden_passed": g["passed"], "turns": out.get("num_turns", 0),
            "tokens": sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")),
            "cost_usd": out.get("total_cost_usd", 0), "seconds": round(time.time() - t0, 1), "summary": (out.get("result") or "")[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["read_only", "with_check"], required=True)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tier", choices=["single", "hard", "all"], default="all", help="single = 14 one-bug tasks; hard = 10 three-bug + from_scratch")
    a = ap.parse_args()
    tasks = sorted(p for p in (ROOT / "tasks").iterdir() if p.is_dir())
    is_hard = lambda p: p.name.startswith("hard_") or p.name == "from_scratch"
    tasks = [p for p in tasks if a.tier == "all" or (a.tier == "hard") == is_hard(p)]
    out = ROOT / "results" / f"{a.tier}_{a.arm}_{a.model}_{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True)
    root = tempfile.mkdtemp(prefix="wal-runs-")
    rows = []
    with ThreadPoolExecutor(a.workers) as ex:
        for r in ex.map(lambda t: run_one(t, a.arm, a.model, a.effort, root), tasks):
            rows.append(r)
            print(f"{'PASS' if r['pass'] else 'FAIL'} {r['task']:26s} hidden {r['hidden_passed']:3d}/300 turns={r['turns']:2d} {r['seconds']:6.1f}s", flush=True)
    n = len(rows)
    summ = {"tier": a.tier, "arm": a.arm, "model": a.model, "tasks": n, "solved": sum(r["pass"] for r in rows),
            "solve_rate": round(sum(r["pass"] for r in rows) / n, 3), "mean_turns": round(sum(r["turns"] for r in rows) / n, 2),
            "mean_tokens": round(sum(r["tokens"] for r in rows) / n), "total_cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 2)}
    (out / "runs.json").write_text(json.dumps(rows, indent=1))
    (out / "summary.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1))
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
