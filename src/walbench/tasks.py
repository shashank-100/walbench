"""WALBench tasks: one injected bug in crash recovery + a user-style bug report. Verified on generation:
the buggy recovery must FAIL the hidden grader, the reference must PASS.

    uv run python -m walbench.tasks       # writes tasks/<id>/{recovery.py, BUG_REPORT.md}
"""
import json
import shutil
from pathlib import Path

from .grader import grade

ROOT = Path(__file__).resolve().parents[2]
REF = (Path(__file__).parent / "recovery_ref.py").read_text()
HIDDEN = {"n": 300, "seed": 7}

# (id, report, [(old, new), ...])
BUGS = [
    ("applies_uncommitted",
     "After a crash, values written by transactions that never committed show up in the store.",
     [("""        else:
            pending.setdefault(rec["txn"], []).append((rec["op"], rec["k"], rec["v"]))""",
       """        else:
            apply_op(state, rec["op"], rec["k"], rec["v"])""")]),
    ("applies_aborted",
     "Transactions that were explicitly aborted reappear after a restart.",
     [("""        elif rec["op"] == "abort":
            pending.pop(rec["txn"], None)""",
       """        elif rec["op"] == "abort":
            for op, k, v in pending.pop(rec["txn"], []):
                apply_op(state, op, k, v)""")]),
    ("trusts_tmp_checkpoint",
     "If the machine dies while a checkpoint is being written, the next startup sometimes fails outright.",
     [("""    path = os.path.join(dirpath, "data.json")
    if not os.path.exists(path):""",
       """    path = os.path.join(dirpath, "data.json")
    if os.path.exists(path + ".tmp"):
        path = path + ".tmp"  # newest checkpoint
    if not os.path.exists(path):""")]),
    ("no_checksum",
     "After a power loss, recovery sometimes crashes with a JSON error or returns garbled values.",
     [("""        if len(payload) < n or zlib.crc32(payload) != crc:""", """        if len(payload) < n:""")]),
    ("torn_tail_raises",
     "After a power loss during a write, startup fails with 'truncated wal' and the store will not open.",
     [("""        if len(payload) < n or zlib.crc32(payload) != crc:
            break""",
       """        if len(payload) < n:
            raise ValueError("truncated wal")
        if zlib.crc32(payload) != crc:
            break""")]),
    ("double_replay",
     "Counters are sometimes higher than they should be after a crash that happened around a checkpoint.",
     [("""        if rec["lsn"] <= applied_lsn:  # already in the checkpoint
            continue
""", "")]),
    ("commit_reorders_ops",
     "Inside one transaction, deleting a key and then writing it again sometimes leaves the key missing after recovery.",
     [("""            for op, k, v in pending.pop(rec["txn"], []):
                apply_op(state, op, k, v)
        elif rec["op"] == "abort":""",
       """            for op, k, v in sorted(pending.pop(rec["txn"], []), key=lambda o: (o[1], o[0])):
                apply_op(state, op, k, v)
        elif rec["op"] == "abort":""")]),
    ("delete_lost",
     "Keys that were deleted come back after a restart.",
     [("""    elif op == "del":
        state.pop(k, None)""", """    elif op == "del":
        pass""")]),
    ("empty_value_deletes",
     "Keys whose value was set to an empty string disappear after recovery.",
     [("""    if op == "put":
        state[k] = v""", """    if op == "put":
        if v == "":
            state.pop(k, None)
        else:
            state[k] = v""")]),
    ("abort_ends_log",
     "Everything written after an aborted transaction is lost after a restart.",
     [("""        elif rec["op"] == "abort":
            pending.pop(rec["txn"], None)""", """        elif rec["op"] == "abort":
            break""")]),
    ("commit_flushes_all",
     "When one transaction commits, writes from other still-open transactions also survive a crash.",
     [("""            for op, k, v in pending.pop(rec["txn"], []):
                apply_op(state, op, k, v)
        elif rec["op"] == "abort":""",
       """            for t in list(pending):
                for op, k, v in pending.pop(t):
                    apply_op(state, op, k, v)
        elif rec["op"] == "abort":""")]),
    ("payload_off_by_one",
     "After any restart, only what was in the last checkpoint is there; everything written since is gone.",
     [("""        payload = wal[pos + HDR.size: pos + HDR.size + n]""", """        payload = wal[pos + HDR.size: pos + HDR.size + n - 1]""")]),
    ("incr_overwrites",
     "Counters come back as small numbers after recovery, as if only the last increment counted.",
     [("""        state[k] = int(state.get(k, 0)) + int(v)""", """        state[k] = int(v)""")]),
    ("skips_checkpoint_boundary",
     "The very first write after each checkpoint is sometimes missing after a crash.",
     [("""        if rec["lsn"] <= applied_lsn:  # already in the checkpoint""", """        if rec["lsn"] <= applied_lsn + 1:  # already in the checkpoint""")]),
]


HARD_REPORT = ("Users report that after crashes and restarts the data is sometimes wrong: values are missing, extra, "
               "or different from what was committed. Several different symptoms have been seen, so there may be more than one cause.")
SCRATCH_STUB = '''"""Crash recovery for the WAL key-value store. Implement recover() according to FORMAT.md."""


def recover(dirpath):
    """Return the recovered key-value state (a dict) for the store in dirpath."""
    raise NotImplementedError
'''


def hard_combos(n=10, k=3, seed=11):
    """Deterministic sets of k bugs whose edits don't overlap, so they can all be injected into one file."""
    import random
    rng, out, tried = random.Random(seed), [], set()
    while len(out) < n:
        combo = tuple(sorted(rng.sample(range(len(BUGS)), k)))
        if combo in tried:
            continue
        tried.add(combo)
        src = REF
        try:
            for i in combo:
                for old, new in BUGS[i][2]:
                    assert src.count(old) == 1
                    src = src.replace(old, new)
        except AssertionError:
            continue
        out.append((combo, src))
    return out


def main():
    out = ROOT / "tasks"
    if out.exists():
        shutil.rmtree(out)
    ok_all = True
    ref = grade(str(Path(__file__).parent / "recovery_ref.py"), **HIDDEN)
    assert ref["pass"], ref
    meta = []
    for tid, report, edits in BUGS:
        src = REF
        for old, new in edits:
            assert src.count(old) == 1, (tid, old[:60])
            src = src.replace(old, new)
        d = out / tid
        d.mkdir(parents=True)
        (d / "recovery.py").write_text(src)
        (d / "BUG_REPORT.md").write_text(f"# Bug report\n\n{report}\n")
        r = grade(str(d / "recovery.py"), **HIDDEN)
        good = not r["pass"]
        ok_all &= good
        meta.append({"id": tid, "report": report, "buggy_passes": r["passed"], "total": r["total"]})
        print(f"{'ok ' if good else 'BAD'} {tid:26s} buggy passes {r['passed']:3d}/{r['total']} hidden scenarios")
    for j, (combo, src) in enumerate(hard_combos()):
        tid = f"hard_{j:02d}"
        d = out / tid
        d.mkdir(parents=True)
        (d / "recovery.py").write_text(src)
        (d / "BUG_REPORT.md").write_text(f"# Bug report\n\n{HARD_REPORT}\n")
        r = grade(str(d / "recovery.py"), **HIDDEN)
        good = not r["pass"]
        ok_all &= good
        meta.append({"id": tid, "bugs": [BUGS[i][0] for i in combo], "buggy_passes": r["passed"], "total": r["total"]})
        print(f"{'ok ' if good else 'BAD'} {tid:26s} bugs {','.join(BUGS[i][0] for i in combo)}  buggy passes {r['passed']}/{r['total']}")
    d = out / "from_scratch"
    d.mkdir(parents=True)
    (d / "recovery.py").write_text(SCRATCH_STUB)
    (d / "BUG_REPORT.md").write_text("# Task\n\nrecovery.py is not implemented yet. Implement recover() so it meets the recovery contract in FORMAT.md.\n")
    r = grade(str(d / "recovery.py"), **HIDDEN)
    ok_all &= not r["pass"]
    meta.append({"id": "from_scratch", "buggy_passes": r["passed"], "total": r["total"]})
    print(f"{'ok ' if not r['pass'] else 'BAD'} from_scratch               stub passes {r['passed']}/{r['total']}")
    (out / "index.json").write_text(json.dumps(meta, indent=1))
    print(f"{len(meta)} tasks, {'all verified' if ok_all else 'SOME TASKS BROKEN'} (reference passes {ref['passed']}/{ref['total']})")


if __name__ == "__main__":
    main()
