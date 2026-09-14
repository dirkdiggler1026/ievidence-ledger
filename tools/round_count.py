#!/usr/bin/env python3
"""Build the backfill round list, validate it against the contract's preconditions,
freeze it to a file, and plan batches.

The spec (§6) names this tool. It exists because the round list is the input to an
all-or-nothing on-chain operation, and the failure modes here are the expensive kind:
a batch that reverts costs every round in it.

Two filters, and they are not the same filter:

  1. canon must be "rhdepth-v2"  — a property of the record.
  2. block numbers must be unique — a property of the set.

Filter 1 alone is the trap. `data/snapshot-v18-12r/rounds.jsonl` holds 12 rounds that
genuinely carry canon=rhdepth-v2 and genuinely duplicate 12 blocks of `2026-09-04` with
identical roundKeccak. Filtering by canon — which is the *correct-looking* filter, and the
one `code/verify.py` uses, with a comment explaining why filtering by record field beats
filtering by directory name — lets all 12 through. Sorted, they become adjacent duplicates,
`blocks` stops being strictly increasing, and the first batch reverts in full.

So: dedupe by block, not only filter by canon. The snapshot is a content-identical copy,
so this is a script problem, not an integrity problem.

Usage:

    python tools/round_count.py --data <executability-report>/data
    python tools/round_count.py --data ../executability-report/data --batch 64
    python tools/round_count.py --data ../executability-report/data --emit backfill-list.jsonl

    # run against the local repo next to this one
    python tools/round_count.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

CANON = "rhdepth-v2"

# Directories that must never enter the backfill set.
#
# NOTE: this list is a belt, not the braces. The braces are the block-number dedupe below.
# A name-based exclusion is exactly what the project's own rules say not to rely on
# ("filter by the fact, not by the name"): the suffixes have already changed once
# (.pre-* became .rhdepth-v1-defective), and the next one will differ again. Keep the
# exclusions for legibility, but never let correctness depend on them.
EXCLUDED_SUFFIXES = (".defective", ".pre-pinned-block", "snapshot-")

EXCLUDED_SUBSTRINGS = (".defective", ".pre-pinned-block", "snapshot-")


def default_data_root() -> Path | None:
    env = os.environ.get("RHDEPTH_DATA")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent.parent
    for cand in (here.parent / "executability-report" / "data", here / "data"):
        if cand.is_dir():
            return cand
    return None


def is_excluded_dir(name: str) -> bool:
    return any(s in name for s in EXCLUDED_SUBSTRINGS)


def collect(data_root: Path, include_excluded: bool = False):
    """Return (kept_by_block, duplicates, skipped_dirs) from every rounds.jsonl."""
    kept: dict[int, dict] = {}
    duplicates: list[dict] = []
    skipped: list[str] = []

    for rf in sorted(data_root.glob("*/rounds.jsonl")):
        day = rf.parent.name
        if is_excluded_dir(day) and not include_excluded:
            skipped.append(day)
            continue
        for line in rf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("canon") != CANON:
                continue
            block = int(rec["block"])
            entry = {
                "block": block,
                "roundKeccak": rec["roundKeccak"],
                "canon": rec["canon"],
                "source": day,
            }
            if block in kept:
                # Same block from two directories. The snapshot copy is content-identical,
                # so this is expected and harmless — but it must be dropped, and if the
                # hashes ever disagree it is a real integrity problem and must not be
                # silently resolved by ordering.
                prev = kept[block]
                duplicates.append(
                    {
                        "block": block,
                        "kept_from": prev["source"],
                        "dropped_from": day,
                        "hash_match": prev["roundKeccak"] == entry["roundKeccak"],
                    }
                )
                continue
            kept[block] = entry

    return kept, duplicates, skipped


def validate(rows: list[dict]) -> list[str]:
    """The contract's preconditions, plus the format checks that make them meaningful."""
    problems: list[str] = []

    blocks = [r["block"] for r in rows]
    if len(set(blocks)) != len(blocks):
        seen, dup = set(), []
        for b in blocks:
            if b in seen:
                dup.append(b)
            seen.add(b)
        problems.append(f"duplicate block numbers survived dedupe: {sorted(set(dup))[:10]}")

    if blocks != sorted(blocks):
        problems.append("rows are not sorted by block")
    if any(b <= a for a, b in zip(blocks, blocks[1:])):
        problems.append("blocks are not strictly increasing")

    hashes = [r["roundKeccak"] for r in rows]
    if len(set(hashes)) != len(hashes):
        problems.append("duplicate roundKeccak across different blocks")

    for r in rows:
        h = r["roundKeccak"]
        if not isinstance(h, str) or not h.startswith("0x"):
            problems.append(f"block {r['block']}: hash is not a 0x string")
        elif len(h) != 66:
            problems.append(f"block {r['block']}: hash is {len(h) - 2} hex chars, not 32 bytes")
        elif int(h, 16) == 0:
            # Zero is *not* rejected by the contract on purpose (canon==0 already marks
            # absence), but a zero hash in the real payload would be a serious defect, so
            # the plan refuses to build one in.
            problems.append(f"block {r['block']}: zero hash")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, default=None, help="path to the data/ directory")
    ap.add_argument("--batch", type=int, default=64, help="rounds per batch (default 64)")
    ap.add_argument("--emit", type=Path, default=None, help="write the frozen round list here")
    ap.add_argument(
        "--include-excluded-dirs",
        action="store_true",
        help="do not skip defective/pre-pinned/snapshot directories (diagnostics only)",
    )
    args = ap.parse_args()

    data_root = args.data or default_data_root()
    if data_root is None or not data_root.is_dir():
        print("data root not found; pass --data <path to executability-report>/data")
        return 2

    kept, duplicates, skipped = collect(data_root, args.include_excluded_dirs)
    rows = [kept[b] for b in sorted(kept)]

    print(f"data root        {data_root}")
    print(f"rounds (canon={CANON}, deduped by block)   {len(rows)}")
    if rows:
        print(f"block range      {rows[0]['block']:,} → {rows[-1]['block']:,}")

    dropped = [d for d in duplicates if is_excluded_dir(d["dropped_from"])]
    other_dup = [d for d in duplicates if not is_excluded_dir(d["dropped_from"])]
    print(f"dropped by name  {len(skipped)} dirs: {', '.join(skipped) if skipped else '(none)'}")
    print(f"dropped by block {len(duplicates)} duplicate rounds")
    if dropped:
        srcs = {}
        for d in dropped:
            srcs.setdefault(d["dropped_from"], 0)
            srcs[d["dropped_from"]] += 1
        for name, n in sorted(srcs.items()):
            print(f"                 {n} from {name} (all hash-identical: "
                  f"{all(x['hash_match'] for x in dropped if x['dropped_from'] == name)})")
    for d in other_dup:
        print(f"                 ⚠️ block {d['block']} duplicated by {d['dropped_from']} "
              f"and {d['kept_from']}, hash_match={d['hash_match']}")

    problems = validate(rows)
    print()
    if problems:
        print("PRECONDITION FAILURES")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("preconditions    OK — strictly increasing, no duplicate blocks or hashes, "
          "no zero hashes, all 32 bytes")

    batch = args.batch
    if batch < 1:
        print("--batch must be >= 1")
        return 2
    n_batches = (len(rows) + batch - 1) // batch
    print()
    print(f"batch plan       {batch} rounds/batch → {n_batches} batches")
    for i in range(n_batches):
        chunk = rows[i * batch : (i + 1) * batch]
        print(f"  batch {i + 1:>3}  {len(chunk):>3} rounds   "
              f"{chunk[0]['block']:,} → {chunk[-1]['block']:,}")

    if args.emit:
        args.emit.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"
        args.emit.write_text(body, encoding="utf-8")
        digest = hashlib.sha256(body.encode()).hexdigest()
        print()
        print(f"frozen list      {args.emit}  ({len(rows)} rows)")
        print(f"sha256           {digest}")
        print("                 run the backfill against this file, not against the directory")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
