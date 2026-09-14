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
