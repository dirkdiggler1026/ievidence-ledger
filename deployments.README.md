# Deployment records

`deployments.jsonl` is **append-only, one line per deployment, in git**. Nothing is ever
edited or removed from it.

It exists because redeployment is a real possibility and not a hypothetical. The ledger's
watermark is monotonic: a single commit at a block far in the future permanently blocks the
append path, every legitimate round then sits below the watermark, there is no reset, and
nothing in the contract can undo it. Redeploying is the remedy. **A silent address swap and a
recorded address swap are different things**, and this file is what makes them different.
See `spec-v1.0.md` §4.2, decision B11.

## Format

One JSON object per line:

```json
{"chainId":46630,"address":"0x…","deployBlock":123456,"deployTxHash":"0x…","canon":"rhdepth-v2","owner":"0x…","solc":"0.8.36","evmVersion":"paris","optimizer":{"enabled":true,"runs":200},"sourceCommit":"bbe4b3c","deployedAt":"2026-09-17T00:00:00Z"}
```

| field | source |
|---|---|
| `chainId` | asserted at deploy time; re-read from the RPC by the recorder |
| `address`, `deployBlock`, `deployTxHash` | the broadcast artifact `forge script --broadcast` wrote |
| `canon` | `LEDGER_CANON`, the value the operator typed; cross-checked against `CANON()` on chain |
| `owner` | `LEDGER_OWNER`, cross-checked against `owner()` on chain |
| `solc`, `evmVersion`, `optimizer` | `foundry.toml` — the same file the build read, and the settings Blockscout verification needs |
| `sourceCommit` | `git rev-parse --short HEAD` at record time |
| `deployedAt` | the deploy block's timestamp, so it is chain-derived rather than wall-clock |

## How a record gets written

Two steps, and the second one checks rather than transcribes:

```sh
export LEDGER_CHAIN_ID=46630
export LEDGER_CANON=rhdepth-v2
export LEDGER_OWNER=0x…            # must equal the broadcasting account

forge script script/Deploy.s.sol --rpc-url $RPC_TESTNET --broadcast
python tools/record_deployment.py --chain-id 46630 --rpc-url $RPC_TESTNET
```

The recorder **refuses to write** if the chain disagrees with the deployment: wrong chain id,
no bytecode at the address, `owner()` ≠ `LEDGER_OWNER`, `CANON()` ≠ 2, or a non-zero
`latestCommittedBlock()` on a fresh deploy. Re-running it is a no-op rather than a duplicate.

`--dry-run` on the recorder verifies and prints without appending.

## What is deliberately not in this file

No rehearsal ever lands here. Deploying to a local chain (`anvil --chain-id 46630`) exercises
the whole path and produces a perfectly well-formed line — and it is still not a deployment.
The first line in this file will be the real 46630 iteration ledger.

## Refusal conditions, both sides

This table is the shared copy. The writer and the reader each enforce the same set for
opposite reasons — the reader must not half-read a record it does not understand, and the
writer must not produce one the reader will reject — and keeping two separate lists would be a
second transcription of one fact, which drifts. If a condition changes, it changes here.

`W` = enforced by `tools/record_deployment.py` before appending.
`R` = enforced by the consumer's reader (`dexfeed/chain/ledger.py`).

| condition | W | R | note |
|---|---|---|---|
| missing field | ✅ | ✅ | |
| unknown extra field | ✅ | ✅ | means one side changed the schema; failing beats writing an unreadable line |
| `address` not `0x` + 40 hex | ✅ | ✅ | |
| `owner` not `0x` + 40 hex | ✅ | ✅ | |
| `deployTxHash` not `0x` + 64 hex | ✅ | ✅ | |
| `deployBlock` not an int, or ≤ 0 | ✅ | ✅ | |
| `chainId` not an int (bool included) | ✅ | ✅ | `True` is an `int` in Python; checked explicitly on both sides |
| `optimizer` not `{enabled: bool, runs: int}` | ✅ | ✅ | |
| `deployedAt` with milliseconds | ✅ | ✅ | second precision, UTC, `Z` |
| `canon` empty | ✅ | ✅ | |
| invalid JSON | n/a | ✅ | the writer emits JSON |
| file missing | n/a | ✅ | reported as "nothing deployed yet", not as a bad path |
| file empty | n/a | ✅ | |
| no record for that chain | n/a | ✅ | |
| `canon` argument ≠ record's canon | n/a | ✅ | |
| chain id mismatch (RPC vs `--chain-id`) | ✅ | n/a | |
| no bytecode at the recorded address | ✅ | n/a | |
| `owner()` on chain ≠ `LEDGER_OWNER` | ✅ | n/a | |
| `CANON()` on chain ≠ 2 | ✅ | n/a | |
| `latestCommittedBlock()` ≠ 0 on a fresh deploy | ✅ | n/a | |
| `LEDGER_CANON` / `LEDGER_OWNER` unset | ✅ | n/a | never defaulted |
| broadcast artifact missing, or no `EvidenceLedger` CREATE | ✅ | n/a | |

**Neither side returns `None`.** A `None` travelling upward is read as "no ledger yet, carry
on", which is the one interpretation that must never be reachable by accident.

Four of the write-side rows above were added after diffing against the reader's list: the
writer previously would have emitted `deployBlock: null`, `deployedAt: null` or
`optimizer.runs: null` when a field could not be derived, and did not check address formats.
Those are all refused now. The diff is the reason they exist; the reader found them by writing
down its own refusals first.

