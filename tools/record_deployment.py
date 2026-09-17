#!/usr/bin/env python3
"""Append one deployment record to deployments.jsonl, after checking it against the chain.

The file is append-only, one line per deployment, and it exists because redeployment is a
real possibility rather than a hypothetical: the ledger's watermark is monotonic, so a
single forward mis-commit (a typo, a mis-read field, a leaked hot key) permanently blocks the
append path and the only remedy is to deploy again. A silent address swap and a recorded
address swap are different things, and this is what makes them different.

Nothing here is transcribed from intent. Every field is either read from the broadcast
artifact that `forge script --broadcast` wrote, or read back from the chain, and the two are
required to agree. If the chain disagrees with the deployment, no record is written.

Usage:

    python tools/record_deployment.py --chain-id 46630 --rpc-url <url>
    python tools/record_deployment.py --chain-id 46630 --rpc-url $RPC_TESTNET --dry-run

Requires (the same values the deployment was given):
    LEDGER_CANON, LEDGER_OWNER
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_NAME = "Deploy"
CONTRACT_NAME = "EvidenceLedger"
DEFAULT_FILE = "deployments.jsonl"
RUN_LATEST = "broadcast/{script}.s.sol/{chain_id}/run-latest.json"

# Console encoding is not guaranteed to be UTF-8 (on Windows it is often GBK), and a refusal
# that crashes while printing its reason is worse than useless: the operator sees a traceback
# instead of which field disagreed. Force UTF-8 where possible and keep the markers ASCII.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# --------------------------------------------------------------------------------------
# JSON-RPC over curl.
#
# Not urllib: on this machine Python's TLS fingerprint is refused by some endpoints while
# curl succeeds against the same URL (403 vs 200, measured). The measurement repository
# learned this the hard way and its rpc() helper shells out to curl for the same reason.
# --------------------------------------------------------------------------------------
def rpc(url: str, method: str, params: list, timeout: int = 30):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    try:
        out = subprocess.run(
            ["curl", "-sS", "-m", str(timeout), "-X", "POST",
             "-H", "Content-Type: application/json", "-d", body, url],
            capture_output=True, text=True, timeout=timeout + 5,
        ).stdout
        data = json.loads(out or "{}")
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return None, f"{type(exc).__name__}: {exc}"
    if "result" in data:
        return data["result"], None
    return None, str((data.get("error") or {}).get("message") or "empty response")


def call_str(url: str, to: str, selector: str):
    """eth_call returning a dynamic string."""
    out, err = rpc(url, "eth_call", [{"to": to, "data": selector}, "latest"])
    if err or not isinstance(out, str) or len(out) < 130:
        return None, err or f"short return: {out!r}"
    body = out[2:]
    try:
        off = int(body[0:64], 16) * 2
        length = int(body[off:off + 64], 16)
        return bytes.fromhex(body[off + 64:off + 64 + length * 2]).decode(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"decode failed: {exc}"


def call_uint(url: str, to: str, selector: str):
    out, err = rpc(url, "eth_call", [{"to": to, "data": selector}, "latest"])
    if err or not isinstance(out, str) or out == "0x":
        return None, err or "empty return"
    return int(out, 16), None


SELECTOR_SIGS = {
    "owner": "owner()",
    "canon": "CANON()",
    "watermark": "latestCommittedBlock()",
}

ARTIFACT = "out/EvidenceLedger.sol/EvidenceLedger.json"


def selectors(root: Path) -> dict[str, str]:
    """Read the selectors from the compiled artifact.

    Not from constants typed by hand: the first version of this file carried two guessed
    selectors and both were wrong (CANON() is c54d5827, latestCommittedBlock() is d2ec073f).
    The artifact carries `methodIdentifiers`, which is derived from the compilation, so it is
    the only source here that cannot drift from the deployed bytecode.
    """
    path = root / ARTIFACT
    if not path.is_file():
        raise SystemExit(
            f"{path} not found. Run `forge build` (or the deployment) first: the selectors "
            "are read from the compiled artifact rather than typed in by hand."
        )
    ids = json.loads(path.read_text(encoding="utf-8")).get("methodIdentifiers") or {}
    out = {}
    for key, sig in SELECTOR_SIGS.items():
        if sig not in ids:
            raise SystemExit(f"artifact has no selector for {sig}; is the ABI what we expect?")
        out[key] = "0x" + ids[sig]
    return out


def find_deployment(root: Path, chain_id: int):
    path = root / RUN_LATEST.format(script=SCRIPT_NAME, chain_id=chain_id)
    if not path.is_file():
        return None, f"broadcast artifact not found: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    receipts = {r.get("transactionHash", "").lower(): r for r in data.get("receipts", [])}
    for tx in data.get("transactions", []):
        if tx.get("contractName") == CONTRACT_NAME and tx.get("transactionType") == "CREATE":
            h = tx.get("hash", "")
            rc = receipts.get(h.lower(), {})
            block = rc.get("blockNumber")
            if isinstance(block, str):
                block = int(block, 16)
            return {
                "address": tx.get("contractAddress"),
                "txHash": h,
                "blockNumber": block,
            }, None
    return None, f"no {CONTRACT_NAME} CREATE transaction in {path}"


def compiler_settings(root: Path):
    with open(root / "foundry.toml", "rb") as fh:
        cfg = tomllib.load(fh).get("profile", {}).get("default", {})
    return {
        "solc": cfg.get("solc_version"),
        "evmVersion": cfg.get("evm_version"),
        "optimizer": {
            "enabled": bool(cfg.get("optimizer", False)),
            "runs": cfg.get("optimizer_runs"),
        },
    }


def git_head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def git_status(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                          capture_output=True, text=True).stdout


def worktree_problems(porcelain: str, exempt: str) -> list[str]:
    """Changes in `git status --porcelain` other than the one exempt path.

    A record's `sourceCommit` claims to name the source that was deployed. On a dirty tree it can
    name a commit that does not contain what is actually running, and nothing in the record would
    show it -- the tool's claim would be wider than its check.

    The exemption is exactly one path, not "untracked files". This file is untracked until it is
    first committed, so a bare check would lock the recorder out of its own second record; but
    exempting every untracked path would also wave through untracked *source*.
    """
    out = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:                     # renames read as "R  old -> new"
            path = path.split(" -> ")[-1].strip().strip('"')
        if path.replace("\\", "/") == exempt.replace("\\", "/"):
            continue
        out.append(line.rstrip())
    return out



# --------------------------------------------------------------------------------------
# The schema, enforced on the way out.
#
# The read side (the consumer) refuses a record with a missing field, an unknown extra field,
# a malformed address/owner/txHash, deployBlock <= 0, a non-integer chainId (bool included),
# a malformed optimizer, milliseconds in deployedAt, or an empty canon. Those checks exist
# because a reader must not half-read a record it does not understand.
#
# The same set is checked here, on the way out, for the opposite reason: a writer must not
# produce a record the reader will reject. Without this, the two sides would disagree only at
# the moment it matters, and the disagreement would look like the reader being wrong.
#
# An unknown extra field is refused in both directions on purpose. It means one side changed
# the schema and the other did not; failing is better than writing a record nobody can read.
# --------------------------------------------------------------------------------------
SCHEMA_KEYS = {
    "chainId", "address", "deployBlock", "deployTxHash", "canon", "owner",
    "solc", "evmVersion", "optimizer", "sourceCommit", "deployedAt",
}

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_record(rec: dict) -> list[str]:
    problems: list[str] = []

    missing = SCHEMA_KEYS - set(rec)
    extra = set(rec) - SCHEMA_KEYS
    if missing:
        problems.append(f"missing fields: {sorted(missing)}")
    if extra:
        problems.append(f"unknown fields: {sorted(extra)} — the reader refuses these by design")

    if not _is_int(rec.get("chainId")):
        problems.append(f"chainId is not an integer: {rec.get('chainId')!r}")
    if not ADDRESS_RE.match(str(rec.get("address") or "")):
        problems.append(f"address is not 20 bytes of hex: {rec.get('address')!r}")
    if not _is_int(rec.get("deployBlock")) or rec["deployBlock"] < 1:
        problems.append(f"deployBlock is not a positive integer: {rec.get('deployBlock')!r}")
    if not HASH_RE.match(str(rec.get("deployTxHash") or "")):
        problems.append(f"deployTxHash is not 32 bytes of hex: {rec.get('deployTxHash')!r}")
    if not isinstance(rec.get("canon"), str) or not rec["canon"].strip():
        problems.append(f"canon is empty or not a string: {rec.get('canon')!r}")
    if not ADDRESS_RE.match(str(rec.get("owner") or "")):
        problems.append(f"owner is not 20 bytes of hex: {rec.get('owner')!r}")
    for key in ("solc", "evmVersion", "sourceCommit"):
        if not isinstance(rec.get(key), str) or not rec[key].strip():
            problems.append(f"{key} is empty or not a string: {rec.get(key)!r}")

    opt = rec.get("optimizer")
    if not isinstance(opt, dict):
        problems.append(f"optimizer is not an object: {opt!r}")
    else:
        if set(opt) != {"enabled", "runs"}:
            problems.append(f"optimizer has unreadable keys: {sorted(opt)}")
        if not isinstance(opt.get("enabled"), bool):
            problems.append(f"optimizer.enabled is not a boolean: {opt.get('enabled')!r}")
        if not _is_int(opt.get("runs")) or opt["runs"] < 0:
            problems.append(f"optimizer.runs is not a non-negative integer: {opt.get('runs')!r}")

    ts = rec.get("deployedAt")
    if not isinstance(ts, str) or not TIMESTAMP_RE.match(ts):
        problems.append(
            f"deployedAt must be second-precision UTC Z, no milliseconds: {ts!r}")
    return problems


def canon_values(root: Path) -> dict[str, int]:
    """The canon name -> value mapping, read from canon.json.

    Not a literal. This file used to carry its own `expected 2`, which is the same fact that
    script/Deploy.s.sol carried in a keccak comparison, in a different language, with nothing
    comparing the two. On the day a new canon arrives, one of them would be updated and the
    other would reject a correct deployment -- or accept the wrong one. Duplication is only
    safe when something checks it; a single source needs no check.

    This also fixes the wording of the error below: it used to read "expected 2 for
    {canon_name}", which looks like 2 is derived from the name. It was not. Now it is.
    """
    path = root / "canon.json"
    if not path.is_file():
        raise SystemExit(f"{path} not found: it is the single source of the canon mapping")
    data = json.loads(path.read_text(encoding="utf-8"))
    canons = data.get("canons")
    if not isinstance(canons, dict) or not canons:
        raise SystemExit(f"{path}: 'canons' must be a non-empty object")
    out: dict[str, int] = {}
    for name, value in canons.items():
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 255:
            raise SystemExit(f"{path}: canon {name!r} must map to an integer in 1..255")
        out[name] = value
    return out


def key_separation_problems(existing: list[str], chain_id: int, owner: str) -> list[str]:
    """A backstop, not the protection.

    What this establishes: the address already owns a *recorded* 46630 ledger.
    What it must not claim: that the address has been through testnet procedure. That is much
    broader. The file cannot see a deployment whose recorder never ran or failed, a faucet
    claim, an env var, a shell history, a test transaction, or an anvil rehearsal -- and
    rehearsals are deliberately never recorded, so the rule that keeps this file honest is the
    same rule that blinds this check. Both rules are right; the consequence is that passing
    here is not evidence that a key is new.

    The protection is timing: generate the mainnet key on the day it is first needed. A key that
    does not exist cannot leak, and this check can only ever show "has not appeared", never
    "is new".

    A rule that lives only in a document is a rule that gets remembered wrong on deploy day, so
    this runs even though it is not sufficient on its own.
    """
    if chain_id != 4663:
        return []
    for line in existing:
        if not line.strip():
            continue
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            continue
        if p.get("chainId") == 46630 and (p.get("owner") or "").lower() == owner.lower():
            return [
                f"owner {owner} already owns a recorded 46630 ledger (recorded at "
                f"{p.get('address')}). The mainnet ledger must be deployed with a key that has "
                f"not been used for testnet. NOTE: this check sees only *recorded* deployments "
                f"-- rehearsals are never recorded by design, and it sees nothing of faucets, "
                f"env vars, shell history or test transactions. Passing it means the address has "
                f"not already owned a ledger we recorded; it is not evidence that the key is new."
            ]
    return []


def selftest(root: Path) -> int:
    """Exercise the write-side refusals. The reader has its own self-test; this is its mirror.

    `root` is passed in rather than taken from the working directory. The first version used
    `Path(".")`, so running `--selftest` from any directory that happened to contain a canon.json
    validated against that file -- and passed. A self-test that can pass against the wrong file
    is the same defect as a check that can pass against the wrong file.
    """
    good = {
        "chainId": 46630,
        "address": "0x" + "a" * 40,
        "deployBlock": 1,
        "deployTxHash": "0x" + "b" * 64,
        "canon": "rhdepth-v2",
        "owner": "0x" + "c" * 40,
        "solc": "0.8.36",
        "evmVersion": "paris",
        "optimizer": {"enabled": True, "runs": 200},
        "sourceCommit": "abc1234",
        "deployedAt": "2026-09-17T00:00:00Z",
    }

    cases: list[tuple[str, dict, bool]] = []

    def variant(**changes) -> dict:
        out = dict(good)
        out.update(changes)
        return out

    cases.append(("well-formed record accepted", good, True))
    cases.append(("missing field refused", {k: v for k, v in good.items() if k != "canon"}, False))
    cases.append(("unknown extra field refused", variant(note="rehearsal"), False))
    cases.append(("short address refused", variant(address="0x1234"), False))
    cases.append(("short tx hash refused", variant(deployTxHash="0x" + "b" * 10), False))
    cases.append(("deployBlock 0 refused", variant(deployBlock=0), False))
    cases.append(("bool chainId refused", variant(chainId=True), False))
    cases.append(("milliseconds in deployedAt refused",
                  variant(deployedAt="2026-09-17T00:00:00.123Z"), False))
    cases.append(("empty canon refused", variant(canon=""), False))
    cases.append(("optimizer.runs as string refused",
                  variant(optimizer={"enabled": True, "runs": "200"}), False))
    cases.append(("optimizer missing runs refused",
                  variant(optimizer={"enabled": True}), False))

    failed = []
    checked = 0

    def record(label: str, ok: bool) -> None:
        """Count every check where it is made, so the total cannot drift from the work.

        The first version hardcoded `total = len(cases) + 4` and was wrong the moment two more
        assertions were added. A count that has to be maintained by hand is the same defect as a
        duplicated fact: it drifts, silently, and it is a claim about the code that the code does
        not enforce.
        """
        nonlocal checked
        checked += 1
        if not ok:
            failed.append(label)

    for name, rec, should_pass in cases:
        problems = validate_record(rec)
        record(f"{name}: expected {'pass' if should_pass else 'refuse'}, got {problems}",
               bool(not problems) == should_pass)

    # canon.json must resolve the name used in every record.
    try:
        values = canon_values(root)
        record("canon.json does not define rhdepth-v2", "rhdepth-v2" in values)
    except SystemExit as exc:
        record(f"canon.json unreadable: {exc}", False)

    # The worktree check, exercised on its own parsing rather than through git.
    dirty = (" M tools/round_count.py\n"
             "?? deployments.jsonl\n"
             "?? tools/untracked_new_tool.py\n"
             "R  docs/old.md -> docs/new.md\n")
    kept = worktree_problems(dirty, "deployments.jsonl")
    record("worktree: the record file itself must be exempt",
           not worktree_problems("?? deployments.jsonl\n", "deployments.jsonl"))
    record("worktree: untracked source must NOT be exempt",
           len(kept) == 3 and any("untracked_new_tool" in k for k in kept))
    record("worktree: a modified tracked file must be caught",
           any("round_count" in k for k in kept))
    record("worktree: a rename must be caught under its new path",
           any("docs/new.md" in k for k in kept))
    record("worktree: a clean tree yields nothing",
           not worktree_problems("", "deployments.jsonl"))

    # Key separation: same owner refused, different owner allowed, testnet not checked -- and
    # the message must claim no more than the check establishes.
    testnet_line = json.dumps({**good, "chainId": 46630, "owner": "0x" + "d" * 40})
    same = key_separation_problems([testnet_line], 4663, "0x" + "d" * 40)
    record("key separation: same owner across chains was not refused", bool(same))
    message = same[0] if same else ""
    record("key separation: message does not say what was established",
           "recorded 46630 ledger" in message)
    record("key separation: message does not state the guard's limit",
           "not evidence that the key is new" in message)
    record("key separation: a different owner was refused",
           not key_separation_problems([testnet_line], 4663, "0x" + "e" * 40))
    record("key separation: testnet deployment should not be checked",
           not key_separation_problems([testnet_line], 46630, "0x" + "d" * 40))

    if failed:
        print(f"SELFTEST FAILED ({len(failed)} of {checked})")
        for f in failed:
            print(f"  x {f}")
        return 1
    print(f"selftest OK ({checked} checks)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chain-id", type=int, default=None)
    ap.add_argument("--rpc-url", default=None,
                    help="defaults to $RPC_MAINNET for 4663, $RPC_TESTNET for 46630")
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--file", type=Path, default=Path(DEFAULT_FILE))
    ap.add_argument("--dry-run", action="store_true", help="verify and print, do not append")
    ap.add_argument("--selftest", action="store_true",
                    help="exercise the write-side refusals; touches no file and no network")
    args = ap.parse_args()

    if args.selftest:
        return selftest(args.root.resolve())

    if args.chain_id is None:
        print("--chain-id is required (4663 or 46630)")
        return 2

    root = args.root.resolve()
    url = args.rpc_url or os.environ.get(
        "RPC_MAINNET" if args.chain_id == 4663 else "RPC_TESTNET")
    if not url:
        print("no rpc url: pass --rpc-url or set RPC_MAINNET / RPC_TESTNET")
        return 2

    canon_name = os.environ.get("LEDGER_CANON")
    if not canon_name:
        print("LEDGER_CANON is not set. The canon is not defaulted anywhere on purpose;")
        print("supply the same value the deployment was given.")
        return 2
    owner_env = os.environ.get("LEDGER_OWNER")
    if not owner_env:
        print("LEDGER_OWNER is not set (the deployment asserted it against the broadcaster).")
        return 2

    dep, err = find_deployment(root, args.chain_id)
    if err:
        print(err)
        return 1

    sel = selectors(root)

    # --- verify against the chain -----------------------------------------------------
    problems = []

    actual_chain, err = rpc(url, "eth_chainId", [])
    if err:
        print(f"eth_chainId failed: {err}")
        return 1
    if int(actual_chain, 16) != args.chain_id:
        problems.append(f"rpc is chain {int(actual_chain, 16)}, not {args.chain_id}")

    code, err = rpc(url, "eth_getCode", [dep["address"], "latest"])
    if err or not code or code == "0x":
        problems.append(f"no bytecode at {dep['address']}")

    owner_onchain, err = call_uint(url, dep["address"], sel["owner"])
    if owner_onchain is None:
        problems.append(f"owner() call failed: {err}")
    else:
        owner_onchain = "0x" + f"{owner_onchain:040x}"
        if owner_onchain.lower() != owner_env.lower():
            problems.append(f"owner() is {owner_onchain}, LEDGER_OWNER is {owner_env}")

    canon_onchain, err = call_uint(url, dep["address"], sel["canon"])
    if canon_onchain is None:
        problems.append(f"CANON() call failed: {err}")
    else:
        expected_canon = canon_values(root).get(canon_name)
        if expected_canon is None:
            problems.append(
                f"canon {canon_name!r} is not in canon.json, so the deployment cannot be "
                f"checked; the chain reports CANON() = {canon_onchain}")
        elif canon_onchain != expected_canon:
            problems.append(
                f"CANON() is {canon_onchain}, but canon.json maps {canon_name!r} to "
                f"{expected_canon}")

    watermark, err = call_uint(url, dep["address"], sel["watermark"])
    if watermark is None:
        problems.append(f"latestCommittedBlock() call failed: {err}")
    elif watermark != 0:
        problems.append(f"latestCommittedBlock() is {watermark}, expected 0 on a fresh deploy")

    block_ts = None
    if dep["blockNumber"] is not None:
        blk, err = rpc(url, "eth_getBlockByNumber", [hex(dep["blockNumber"]), False])
        if isinstance(blk, dict) and blk.get("timestamp"):
            block_ts = int(blk["timestamp"], 16)

    if problems:
        print("REFUSING TO RECORD - the chain disagrees with the deployment:")
        for p in problems:
            print(f"  x {p}")
        return 1

    target = root / args.file
    existing = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []

    separation = key_separation_problems(existing, args.chain_id, owner_env)
    if separation:
        print("REFUSING TO RECORD - key separation violated:")
        for p in separation:
            print(f"  x {p}")
        return 1

    cs = compiler_settings(root)

    # Before anything is written: a dirty tree makes `sourceCommit` a claim the check cannot
    # support. Refuse, and name every offending path rather than the first.
    exempt = os.path.relpath(target, root).replace("\\", "/")
    dirty = worktree_problems(git_status(root), exempt)
    if dirty:
        print("REFUSING TO RECORD - the worktree is not clean, so sourceCommit would name a")
        print(f"commit that does not contain what was deployed. (only {exempt!r} is exempt)")
        for d in dirty[:20]:
            print(f"  x {d}")
        if len(dirty) > 20:
            print(f"  ... and {len(dirty) - 20} more")
        return 1

    record = {
        "chainId": args.chain_id,
        "address": dep["address"],
        "deployBlock": dep["blockNumber"],
        "deployTxHash": dep["txHash"],
        "canon": canon_name,
        "owner": owner_env,
        "solc": cs["solc"],
        "evmVersion": cs["evmVersion"],
        "optimizer": cs["optimizer"],
        "sourceCommit": git_head(root),
        "deployedAt": datetime.fromtimestamp(block_ts, tz=timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ") if block_ts else None,
    }

    # The reader refuses a record it cannot fully understand. Refuse to produce one.
    shape = validate_record(record)
    if shape:
        print("REFUSING TO RECORD - this record would not be readable:")
        for p in shape:
            print(f"  x {p}")
        return 1

    line = json.dumps(record, separators=(",", ":"))

    # --- append, idempotently ---------------------------------------------------------
    for prev in existing:
        if not prev.strip():
            continue
        try:
            p = json.loads(prev)
        except json.JSONDecodeError:
            continue
        if (p.get("chainId"), (p.get("address") or "").lower(),
                (p.get("deployTxHash") or "").lower()) == (
                record["chainId"], record["address"].lower(), record["deployTxHash"].lower()):
            print("already recorded, nothing appended:")
            print(line)
            return 0

    if args.dry_run:
        print("verified against chain; --dry-run so not appended:")
        print(line)
        return 0

    with open(target, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
    print(f"verified against chain and appended to {target}:")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
