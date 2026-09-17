"""Validate a frozen backfill list before anything is committed to the ledger.

Why this exists separately from the submit script: `commitBatch` advances a monotonic watermark
that cannot be moved backwards. A wrong row order or a wrong hash is not a failure that can be
retried away -- it permanently poisons the ledger, and on mainnet it poisons the artefact third
parties verify. So the list is checked before anything is built, and this check needs no key, no
broadcast and no chain write.

It cross-checks each entry against the published data rather than against the list itself: a list
that agrees with itself proves nothing. The published `rounds.jsonl` is the authority for what a
round's hash is.

Usage:
    python tools/check_backfill_list.py [--list backfill-list.jsonl] [--data <executability-report>/data]
    python tools/check_backfill_list.py --emit backfill-checked.json

`--emit` writes a record of what was checked, including the sha256 of the list's bytes. The submit
script refuses to enter its broadcast path unless that record exists and its hash matches the list
it is about to commit. So "the list was validated" becomes a mechanical precondition rather than a
step someone has to remember under time pressure -- the same reason the validator refuses on
contradictions only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

CANON = "rhdepth-v2"
BATCH = 64                      # the rule, not a count: one batch is all-or-nothing, so its size
                                # is the failure radius (see MEASUREMENTS.md)
ROOT = Path(__file__).resolve().parent.parent


def canon_values() -> dict[str, int]:
    """Name -> value from canon.json, the single source shared with the Solidity scripts."""
    data = json.loads((ROOT / "canon.json").read_text(encoding="utf-8"))
    return {k: int(v) for k, v in data["canons"].items()}


def load_published(data: Path) -> dict[int, str]:
    """block -> roundKeccak, from every published directory, for the frozen canon only."""
    out: dict[int, str] = {}
    for rf in sorted(data.glob("*/rounds.jsonl")):
        for line in rf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("canon") != CANON:
                continue
            out[int(r["block"])] = str(r["roundKeccak"]).lower()
    return out


def selftest() -> int:
    """Every check, one case each, in a temp dir with a small published data set.

    The first version ran every case against an empty data directory, so every entry was
    "unpublished" and every negative case was refused by the unpublished count alone. Deleting
    the ordering, duplicate, canon, hash-length or contradiction check left it green. So here
    every refusal case is otherwise valid and fully published, and the refusal REASON is
    asserted, not only the exit code: a case refused for another reason proves nothing about
    the check it is named after.
    """
    import subprocess
    import tempfile

    me = Path(__file__).resolve()
    ok = True
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        day = t / "data" / "2026-09-04"
        day.mkdir(parents=True)
        a = {"block": 54088399, "canon": CANON, "roundKeccak": "0x" + "11" * 32}
        b = {"block": 54088400, "canon": CANON, "roundKeccak": "0x" + "22" * 32}
        new = {"block": 99000000, "canon": CANON, "roundKeccak": "0x" + "cd" * 32}
        (day / "rounds.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in (a, b)), encoding="utf-8", newline="\n")

        cases = [
            # name, rows, extra argv, reason that must appear (None = must accept), line ending
            ("published pair", [a, b], [], None, "\n"),
            ("one unpublished, declared", [a, b, new], ["--allow-unpublished", "1"], None, "\n"),
            ("one unpublished, undeclared", [a, b, new], [],
             "absent from the published data", "\n"),
            ("one unpublished, wrong count", [a, b, new], ["--allow-unpublished", "2"],
             "absent from the published data", "\n"),
            ("empty list", [], [], "no entries", "\n"),
            ("hash contradicts published", [a, dict(b, roundKeccak="0x" + "33" * 32)], [],
             "CONTRADICT", "\n"),
            ("descending", [b, a], [], "not in ascending order", "\n"),
            ("duplicate block", [a, a], [], "duplicate blocks", "\n"),
            ("bad canon", [dict(a, canon="rhdepth-v1"), b], [], "expected 'rhdepth-v2'", "\n"),
            ("short hash", [dict(a, roundKeccak="0x" + "11" * 16), b], [], "not 32 bytes", "\n"),
            ("hash is not hex", [a, dict(b, roundKeccak="0x" + "zz" * 32)], [], "not hex", "\n"),
            ("block above uint64", [a, dict(b, block=2 ** 64)], [], "uint64 range", "\n"),
            ("CRLF line endings", [a, b], [], "CR bytes", "\r\n"),
        ]
        for i, (name, rows, extra, reason, eol) in enumerate(cases):
            lst = t / f"case{i}.jsonl"
            lst.write_bytes("".join(json.dumps(r, sort_keys=True) + eol for r in rows).encode())
            rec = t / f"case{i}.json"
            cmd = [sys.executable, str(me), "--list", str(lst), "--data", str(t / "data"),
                   "--emit", str(rec), *extra]
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            wrote = rec.exists()
            if reason is None:
                good_case = p.returncode == 0 and wrote
            else:
                good_case = p.returncode == 1 and not wrote and reason in p.stdout
            ok = ok and good_case
            print(f"  {'ok  ' if good_case else 'FAIL'}  {name:<32} exit={p.returncode} "
                  f"record={wrote} expected={'accept' if reason is None else 'refuse: ' + reason}")
    print("selftest: " + ("every check refused its own case, and the valid lists were accepted"
                          if ok else "SOME CASES ARE WRONG"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", type=Path, default=Path("backfill-list.jsonl"))
    ap.add_argument("--data", type=Path, default=Path(r"E:\executability-report\data"))
    ap.add_argument("--selftest", action="store_true",
                    help="run the negative cases in a temp dir; a two-round temp data set, "
                         "no network, no key")
    ap.add_argument("--emit", type=Path, default=None,
                    help="write the checked record here (only when the list passes)")
    ap.add_argument("--allow-unpublished", type=int, default=0, metavar="N",
                    help="exactly how many entries may be absent from the published data "
                         "(default 0). An entry nobody cross-checked is not a verified entry, "
                         "so the count is stated rather than assumed.")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.list.is_file():
        print(f"{args.list} not found")
        return 1
    entries = []
    for i, line in enumerate(args.list.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                entries.append((i, json.loads(line)))
            except json.JSONDecodeError as e:
                print(f"line {i}: not JSON: {e}")
                return 1

    problems: list[str] = []
    # round_count.py refuses to write CR bytes, but the gate is here: splitlines() above
    # accepts CRLF, while listSha256 is over file bytes. A list converted on the way (an
    # editor, a transfer) would otherwise get a record for a different artefact.
    if b"\r" in args.list.read_bytes():
        problems.append("the list contains CR bytes: its sha256 describes a CRLF file, not "
                        "the LF artefact round_count.py emits")
    print(f"list   {args.list}   {len(entries)} entries")

    # shape
    for i, e in entries:
        if not isinstance(e.get("block"), int):
            problems.append(f"line {i}: block is not an integer: {e.get('block')!r}")
        elif not (0 < e["block"] < 2 ** 64):
            problems.append(f"line {i}: block out of uint64 range: {e['block']}")
        if e.get("canon") != CANON:
            problems.append(f"line {i}: canon is {e.get('canon')!r}, expected {CANON!r}")
        h = str(e.get("roundKeccak", ""))
        if len(h) != 66 or not h.startswith("0x"):
            problems.append(f"line {i}: roundKeccak is not 32 bytes: {h!r}")
        else:
            try:
                int(h, 16)
            except ValueError:
                problems.append(f"line {i}: roundKeccak is not hex: {h!r}")

    blocks = [e["block"] for _, e in entries if isinstance(e.get("block"), int)]
    if blocks != sorted(blocks):
        problems.append("blocks are not in ascending order")
    if len(set(blocks)) != len(blocks):
        dupes = sorted({b for b in blocks if blocks.count(b) > 1})
        problems.append(f"duplicate blocks: {dupes[:0] or dupes}")

    # Against the published data. Two different findings, and they must not be conflated:
    #   a block that IS published with a different hash  -> a contradiction, refuse
    #   a block that is NOT published                    -> often legitimate: the execution-day
    #        list is generated from the authoritative directory, which can be fresher than the
    #        public repo. Reporting it as an error would make the check unusable on the day it
    #        is needed. So it is reported, with the days, and a human decides.
    published = load_published(args.data)
    print(f"published v2 rounds available: {len(published)}")

    mismatched = [(b, published[b], h)
                  for _, e in entries if isinstance(e.get("block"), int)
                  for b, h in [(e["block"], str(e["roundKeccak"]).lower())]
                  if b in published and published[b] != h]
    if mismatched:
        problems.append(f"{len(mismatched)} roundKeccak values CONTRADICT the published data, "
                        f"e.g. {mismatched[:2]}")

    absent = [b for b in blocks if b not in published]
    if not blocks:
        problems.append("the list has no entries; a record for an empty list opens the submit "
                        "gate while committing nothing")
    if len(absent) != args.allow_unpublished:
        problems.append(
            f"{len(absent)} entries are absent from the published data but --allow-unpublished "
            f"is {args.allow_unpublished}. Those entries cannot be cross-checked by definition, "
            f"so they must be counted explicitly: first few "
            f"{[f'{b:,}' for b in absent[:5]]}")
    if absent:
        print(f"\nblocks in the list but not yet published: {len(absent)}  "
              f"(declared with --allow-unpublished {args.allow_unpublished})")
        print("  first few:", [f"{b:,}" for b in absent[:5]])
        print("  these hashes were NOT compared against anything. They go on chain as given.")

    missing_from_list = sorted(set(published) - set(blocks))
    if missing_from_list:
        by_day = {}
        for rf in sorted(args.data.glob("*/rounds.jsonl")):
            day = rf.parent.name
            if len(day) != 10:
                continue
            day_blocks = {int(json.loads(l)["block"])
                          for l in rf.read_text(encoding="utf-8").splitlines()
                          if l.strip() and json.loads(l).get("canon") == CANON}
            n = len(day_blocks & set(missing_from_list))
            if n:
                by_day[day] = n
        print(f"\npublished rounds NOT in the list: {len(missing_from_list)}   by day {by_day}")
        print("  expected if the list is pinned to a later start, or if the list is stale;")
        print("  the operator decides. It is reported because a silently truncated list is")
        print("  exactly the failure that cannot be undone after the watermark moves.")

    print(f"\nbatches at {BATCH}/batch: {-(-len(blocks) // BATCH)}"
          f"   (last batch {len(blocks) % BATCH or BATCH} entries)")
    print("each batch is all-or-nothing, so a failed batch is re-run from the watermark, never"
          " from the start")

    if problems:
        print(f"\nREFUSING: {len(problems)} problem(s)")
        for p in problems:
            print(f"  x {p}")
        print("\nno record written: the submit path stays closed")
        return 1

    values = canon_values()
    if CANON not in values:
        print(f"\nREFUSING: canon.json does not map {CANON!r}, so the submit script could not check"
              f" the ledger's CANON() against it")
        return 1

    checked = len(blocks) - len(absent)
    if absent:
        print(f"\nLIST OK: shape, ordering and canon are valid. {checked} of {len(blocks)} hashes "
              f"agree with the published data; {len(absent)} were NOT cross-checked.")
    else:
        print(f"\nLIST OK: shape, ordering, canon and all {checked} hashes agree with the "
              f"published data")
    if args.emit:
        record = {
            "list": str(args.list),
            "listSha256": "0x" + hashlib.sha256(args.list.read_bytes()).hexdigest(),
            "entries": len(blocks),
            "firstBlock": blocks[0] if blocks else None,
            "lastBlock": blocks[-1] if blocks else None,
            "canon": CANON,
            "canonValue": values[CANON],
            "batch": BATCH,
            "unpublishedEntries": len(absent),
            # Named for what it is: the first ten, not the list. A field called
            # `unpublishedBlocks` that silently stops at ten is the naming rule this project
            # keeps re-learning -- a name must not promise more than the thing establishes.
            "unpublishedBlocksFirst10": absent[:10],
            "checkedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checker": "tools/check_backfill_list.py",
        }
        args.emit.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"record written to {args.emit}")
        print(json.dumps(record, indent=2))
        print("\nthe submit script refuses to broadcast unless this record's listSha256 equals the")
        print("sha256 of the list it reads, so an unvalidated list cannot reach the chain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
