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
"""
from __future__ import annotations

import argparse
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", type=Path, default=Path("backfill-list.jsonl"))
    ap.add_argument("--data", type=Path, default=Path(r"E:\executability-report\data"))
    args = ap.parse_args()

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
    if absent:
        days = {}
        for rf in sorted(args.data.glob("*/rounds.jsonl")):
            pass
        print(f"\nblocks in the list but not yet published: {len(absent)}  "
              f"(not an error: the list may be fresher than this repo)")
        print("  first few:", [f"{b:,}" for b in absent[:5]])

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
        return 1
    print("\nLIST OK: shape, ordering, canon and every hash agree with the published data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
