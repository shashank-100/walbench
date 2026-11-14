"""Reference (correct) crash recovery. Every task ships a copy of this file with one bug injected."""
import json
import os
import struct
import zlib

HDR = struct.Struct("<II")


def apply_op(state, op, k, v):
    if op == "put":
        state[k] = v
    elif op == "del":
        state.pop(k, None)
    elif op == "incr":
        state[k] = int(state.get(k, 0)) + int(v)


def load_checkpoint(dirpath):
    """The last completed checkpoint. A leftover data.json.tmp is an unfinished checkpoint and must be ignored."""
    path = os.path.join(dirpath, "data.json")
    if os.path.exists(path + ".tmp"):
        path = path + ".tmp"  # newest checkpoint
    if not os.path.exists(path):
        return {}, 0
    with open(path) as f:
        d = json.load(f)
    return dict(d["state"]), d["applied_lsn"]


def read_wal(dirpath):
    """Intact records in order. A short or checksum-failing record ends the log."""
    path = os.path.join(dirpath, "wal.log")
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        wal = f.read()
    out, pos = [], 0
    while pos + HDR.size <= len(wal):
        n, crc = HDR.unpack_from(wal, pos)
        payload = wal[pos + HDR.size: pos + HDR.size + n]
        if len(payload) < n or zlib.crc32(payload) != crc:
            break
        out.append(json.loads(payload))
        pos += HDR.size + n
    return out


def recover(dirpath):
    state, applied_lsn = load_checkpoint(dirpath)
    pending = {}
    for rec in read_wal(dirpath):
        if rec["lsn"] <= applied_lsn:  # already in the checkpoint
            continue
        if rec["op"] == "commit":
            for op, k, v in pending.pop(rec["txn"], []):
                apply_op(state, op, k, v)
        elif rec["op"] == "abort":
            pending.pop(rec["txn"], None)
        else:
            pending.setdefault(rec["txn"], []).append((rec["op"], rec["k"], rec["v"]))
    return state
