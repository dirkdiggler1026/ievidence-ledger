# IEvidenceLedger

An evidence ledger for executable-depth measurements. Each measurement round is reduced
to a canonical record set, hashed, and anchored, so a published number can be checked
after the fact by anyone instead of trusted.

The data source is the public measurement series maintained in
[executability-report](https://github.com/dirkdiggler1026/executability-report).

## Status

- **Specification freeze: 2026-09-13** (`spec-v1.0.md`; draft-final until that commit).
- **Implementation begins 2026-09-14**, with the Open House Singapore buildathon window
  (2026-09-14 → 2026-10-04). There is no code in this repository before that date, and
  the commit history is the record of it.
- **Deployed 2026-09-15 to Robinhood Chain testnet (chainId 46630):**
  `0xc4f7c2ed489d9f521d65b43cc4929d3c642c6fb9`, deployment block 119878215, `canon`
  `rhdepth-v2` (`CANON()` returns 2, `owner()` returns the deploying account). As of
  2026-09-16 the ledger is deployed and **empty**: `latestCommittedBlock()` returns 0
  and `getRoundHash()` returns canon 0 (absent) for every block. Nothing is anchored
  yet, and no published measurement claims otherwise.

`deployments.jsonl` is the append-only record of deployments, one line each, and
`deployments.README.md` says what a line means and which checks refuse a bad one.

| file | what it is |
|---|---|
| `spec-v1.0.md` | contract interface, canonical serialization, anchor model, demo scope |
| `SPEC-DECISIONS.md` | the decision log behind the spec, including what was rejected and why |
| `MEASUREMENTS.md` | measured slots and gas per round; what the on-chain figures are and are not |
| `src/` | `IEvidenceLedger.sol` (the interface) and `EvidenceLedger.sol` |
| `script/` | `Deploy.s.sol`; `Commit.s.sol`, which commits a frozen round list in batches |
| `test/` | 29 tests; `Gas.t.sol` measures the per-round gas `MEASUREMENTS.md` cites |
| `tools/` | record a deployment, export the ABI, check a backfill list, count rounds |
| `deployments.jsonl` | append-only, one line per deployment |

## Cost

Two slots and ~45,650 gas per round, measured rather than estimated; one slot is written on
top of that per batch. Neither gas nor calldata size constrains batch size on this chain —
the binding constraint is how much work a failed batch would cost. See `MEASUREMENTS.md`.

## Anchor model

The ledger writes one hash per measurement round, derived only from quantities a third
party can reproduce at the same block height. It stores no measurement data: the data
stays in the repository above, and the ledger is the notary, not the warehouse. See
`spec-v1.0.md` §2, §3 and §5.

## Check it yourself

Checking this repository needs no key, account or archive node:

```sh
forge test

cast call 0xc4f7c2ed489d9f521d65b43cc4929d3c642c6fb9 \
  "latestCommittedBlock()(uint64)" \
  --rpc-url https://rpc.testnet.chain.robinhood.com

cast call 0xc4f7c2ed489d9f521d65b43cc4929d3c642c6fb9 \
  "getRoundHash(uint64)(bytes32,uint8)" 61129566 \
  --rpc-url https://rpc.testnet.chain.robinhood.com
```

The `getRoundHash` call currently returns canon 0, which means **not committed** — not a
hash of zero, and not a claim that the round failed. That block is a real published
round (`data/2026-09-12/rounds.jsonl` in the report repository). Recomputing a round
hash from the published data is a different job and needs an archive node; the report
repository describes it under
[Verify it yourself](https://github.com/dirkdiggler1026/executability-report#verify-it-yourself).

## License

Code: Apache License 2.0 (`LICENSE`). Specification and documents: CC BY 4.0. See `NOTICE`.

## Contact

dirkdiggler871026@gmail.com
