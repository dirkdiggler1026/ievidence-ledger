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
  `rhdepth-v2` (`CANON()` returns 2, `owner()` returns the deploying account).
- **Backfilled 2026-09-19:** 610 published rounds committed in ten transactions.
  `latestCommittedBlock()` has returned **64,907,249** since.
- **Deployed 2026-09-23 to Robinhood Chain mainnet (chainId 4663):**
  `0x7f5446b920e09531f443ce951076cbaed09dfab6`, block 70,145,346, `canon` `rhdepth-v2`,
  owner `0x6768cEF3…` — a key that has never held the testnet ledger. 850 published rounds
  through block 69,196,861 were committed in fourteen transactions.

A round at or below a ledger's watermark returns its hash with `canon 2`. A round above the
watermark — in practice everything published after 2026-09-16 — and any quarantined directory
returns `canon 0` ("absent") on that ledger, because the backfill list ends at the watermark.
That is a statement about the ledger's coverage, not about the round.

An earlier version of this section said the ledger was deployed and **empty**, that
`getRoundHash()` returned canon 0 for every block, and that nothing was anchored. It was written
on 2026-09-16 and was true then. The backfill landed on 2026-09-19 and the mainnet ledger on
2026-09-23. The report pages, the demo cards and the film do claim anchoring, and this repository
is what they point at — so this paragraph had to change rather than the claim.

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

# mainnet, chainId 4663 -- 850 published rounds through block 69,196,861
cast call 0x7f5446b920e09531f443ce951076cbaed09dfab6 \
  "latestCommittedBlock()(uint64)" \
  --rpc-url https://rpc.mainnet.chain.robinhood.com

cast call 0x7f5446b920e09531f443ce951076cbaed09dfab6 \
  "getRoundHash(uint64)(bytes32,uint8)" 61129566 \
  --rpc-url https://rpc.mainnet.chain.robinhood.com

# testnet, chainId 46630 -- the same contract, 610 rounds through block 64,907,249
cast call 0xc4f7c2ed489d9f521d65b43cc4929d3c642c6fb9 \
  "latestCommittedBlock()(uint64)" \
  --rpc-url https://rpc.testnet.chain.robinhood.com
```

`getRoundHash(61129566)` returns `0x2c645beb…21a2d2` with `canon 2` — measured on testnet, and
that round sits inside the mainnet backfill's range as well. The value is not a new number: it is
the `roundKeccak` the report already published for that round, in
`data/2026-09-12/rounds.jsonl`, returned by the chain instead of by the publisher. That is what
anchoring means, and checking it needs no archive node.

An earlier version of this paragraph said the call returned canon 0, "which means not committed".
That was true before 2026-09-19 and is not true now; leaving it would have told a reader that the
project's own anchor contradicts the project's own film.

Recomputing a round hash from the published data is a different job and needs an archive node;
the report repository describes it under
[Verify it yourself](https://github.com/dirkdiggler1026/executability-report#verify-it-yourself).

## License

Code: Apache License 2.0 (`LICENSE`). Specification and documents: CC BY 4.0. See `NOTICE`.

## Contact

dirkdiggler871026@gmail.com
