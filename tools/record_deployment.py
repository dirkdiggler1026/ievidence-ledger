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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chain-id", type=int, required=True)
    ap.add_argument("--rpc-url", default=None,
                    help="defaults to $RPC_MAINNET for 4663, $RPC_TESTNET for 46630")
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--file", type=Path, default=Path(DEFAULT_FILE))
    ap.add_argument("--dry-run", action="store_true", help="verify and print, do not append")
    args = ap.parse_args()

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
    elif canon_onchain != 2:
        problems.append(f"CANON() is {canon_onchain}, expected 2 for {canon_name}")

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

    cs = compiler_settings(root)
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
    line = json.dumps(record, separators=(",", ":"))

    # --- append, idempotently ---------------------------------------------------------
    target = root / args.file
    existing = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
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
