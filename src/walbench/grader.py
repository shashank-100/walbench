"""Grade a recovery module: run it on seeded crash scenarios and compare with the independent model in engine.Sim.

    python -m walbench.grader path/to/recovery.py [--n 300] [--seed 0]
"""
import argparse
import importlib.util
import json
import random
import sys
import tempfile
import traceback
from pathlib import Path

from .engine import Sim


def load_module(path):
    spec = importlib.util.spec_from_file_location(f"cand_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scenarios(n, seed):
    rng = random.Random(seed)
    for i in range(n):
        sim = Sim(seed * 100_003 + i, n_txns=rng.randint(6, 18))
        yield i, sim.crash(rng)


def grade(path, n=300, seed=0, show=1):
    try:
        mod = load_module(path)
    except Exception as e:
        return {"passed": 0, "total": n, "pass": False, "error": f"import failed: {e}"}
    passed, fails = 0, []
    for i, (files, expected) in scenarios(n, seed):
        with tempfile.TemporaryDirectory() as d:
            for name, data in files.items():
                Path(d, name).write_bytes(data)
            try:
                got = mod.recover(d)
                ok = got == expected
                err = None
            except Exception:
                ok, got, err = False, None, traceback.format_exc(limit=2)
        passed += ok
        if not ok and len(fails) < show:
            fails.append({"scenario": i, "files": sorted(files), "wal_bytes": len(files["wal.log"]),
                          "expected": expected, "got": got, "error": err})
    return {"passed": passed, "total": n, "pass": passed == n, "failures": fails}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--show", type=int, default=1)
    a = ap.parse_args()
    r = grade(a.path, a.n, a.seed, a.show)
    print(json.dumps(r, indent=1, default=str))
    sys.exit(0 if r["pass"] else 1)


if __name__ == "__main__":
    main()
