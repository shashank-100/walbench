"""A small write-ahead-logged key-value store, a deterministic crash simulator, and an independent model of what must
survive each crash.

On disk (one directory):
  wal.log         records: [u32 length][u32 crc32(payload)][payload = JSON {"lsn","txn","op","k","v"}]
                  ops: put, del, incr (non-idempotent: replaying it twice is visible), commit, abort
  data.json       checkpoint: {"applied_lsn": n, "state": {...}}; written as data.json.tmp, then renamed, then the WAL is
                  truncated. A crash can land between any two of those steps.

Recovery contract: the recovered state contains exactly the effects of transactions whose commit record is durable,
applied in commit order, each exactly once; nothing from aborted or unfinished transactions; a torn or corrupt record
ends the log (it and everything after it is ignored).
"""
import json
import random
import struct
import zlib

HDR = struct.Struct("<II")


def encode(rec: dict) -> bytes:
    p = json.dumps(rec, separators=(",", ":"), sort_keys=True).encode()
    return HDR.pack(len(p), zlib.crc32(p)) + p


def apply_op(state, op, k, v):
    if op == "put":
        state[k] = v
    elif op == "del":
        state.pop(k, None)
    elif op == "incr":
        state[k] = int(state.get(k, 0)) + int(v)


class Sim:
    """Runs a seeded workload, recording the exact disk contents after every write, plus what is durably committed."""

    def __init__(self, seed: int, n_txns: int = 14):
        self.rng = random.Random(seed)
        self.wal, self.data, self.tmp = b"", None, None
        self.lsn, self.applied_lsn = 0, 0
        self.disk_states = []  # (wal, data, tmp, expected_state, record_boundaries)
        self.committed_state, self.ckpt_state = {}, {}
        self.boundaries = [0]
        self._run(n_txns)

    def _snap(self):
        self.disk_states.append((self.wal, self.data, self.tmp, dict(self.committed_state), list(self.boundaries)))

    def _append(self, rec):
        self.lsn += 1
        rec = {"lsn": self.lsn, **rec}
        self.wal += encode(rec)
        self.boundaries.append(len(self.wal))
        return rec

    def _run(self, n_txns):
        r, keys = self.rng, [f"k{i}" for i in range(6)]
        open_txns, next_id, done, pending = {}, 1, 0, {}
        self._snap()
        while done < n_txns:
            if open_txns and (len(open_txns) >= 3 or r.random() < 0.45):
                t = r.choice(sorted(open_txns))
                ops = open_txns.pop(t)
                if r.random() < 0.15:
                    self._append({"txn": t, "op": "abort", "k": None, "v": None})
                else:
                    self._append({"txn": t, "op": "commit", "k": None, "v": None})
                    for op, k, v in ops:
                        apply_op(self.committed_state, op, k, v)
                done += 1
                self._snap()
            elif r.random() < 0.12 and not open_txns and self.wal:
                self._checkpoint()
            else:
                if not open_txns or (len(open_txns) < 3 and r.random() < 0.4):
                    open_txns[next_id] = []
                    next_id += 1
                t = r.choice(sorted(open_txns))
                op = r.choice(["put", "put", "del", "incr", "incr"])
                k = r.choice(["c0", "c1", "c2"]) if op == "incr" else r.choice(keys)  # counters and strings never mix
                v = r.choice(["a", "b", "", "zz", "x y"]) if op == "put" else (r.randint(1, 9) if op == "incr" else None)
                self._append({"txn": t, "op": op, "k": k, "v": v})
                open_txns[t].append((op, k, v))
                self._snap()

    def _checkpoint(self):
        payload = json.dumps({"applied_lsn": self.lsn, "state": self.committed_state}, sort_keys=True)
        self.tmp = payload[: max(1, len(payload) // 2)]
        self._snap()                       # crash here: half-written tmp file, data.json is the old one
        self.tmp = payload
        self._snap()                       # crash here: tmp complete, not yet renamed
        self.data, self.tmp = payload, None
        self.applied_lsn = self.lsn
        self._snap()                       # crash here: new data.json, WAL not yet truncated (old records <= applied_lsn)
        self.wal, self.boundaries = b"", [0]
        self._snap()

    def crash(self, rng):
        """A random crash: a recorded disk state, optionally with a torn or corrupted tail. Returns (files, expected)."""
        wal, data, tmp, committed, bounds = rng.choice(self.disk_states)
        kind = rng.random()
        if wal and kind < 0.3:  # torn write: cut inside the last record
            last = bounds[-2] if len(bounds) > 1 else 0
            if len(wal) - last > 1:
                cut = rng.randint(last + 1, len(wal) - 1)
                committed = self._expected(wal[:cut], data)
                wal = wal[:cut]
        elif wal and kind < 0.4:  # bit flip inside the last record's payload
            last = bounds[-2] if len(bounds) > 1 else 0
            if len(wal) - last > HDR.size + 1:
                pos = rng.randint(last + HDR.size, len(wal) - 1)
                wal = wal[:pos] + bytes([wal[pos] ^ 0x5A]) + wal[pos + 1:]
                committed = self._expected(wal, data)
        files = {"wal.log": wal}
        if data is not None:
            files["data.json"] = data.encode()
        if tmp is not None:
            files["data.json.tmp"] = tmp.encode()
        return files, committed

    @staticmethod
    def _expected(wal, data):
        """Independent model: checkpoint state + committed txns whose commit record is fully intact, in commit order."""
        state, applied = ({}, 0)
        if data is not None:
            d = json.loads(data)
            state, applied = dict(d["state"]), d["applied_lsn"]
        pos, ops = 0, {}
        while pos + HDR.size <= len(wal):
            n, crc = HDR.unpack_from(wal, pos)
            p = wal[pos + HDR.size: pos + HDR.size + n]
            if len(p) < n or zlib.crc32(p) != crc:
                break
            rec = json.loads(p)
            pos += HDR.size + n
            if rec["lsn"] <= applied:
                continue
            if rec["op"] == "commit":
                for op, k, v in ops.pop(rec["txn"], []):
                    apply_op(state, op, k, v)
            elif rec["op"] == "abort":
                ops.pop(rec["txn"], None)
            else:
                ops.setdefault(rec["txn"], []).append((rec["op"], rec["k"], rec["v"]))
        return state
