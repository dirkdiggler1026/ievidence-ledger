# Cost measurements

Two quantities are measured here, and only two, because they are the only ones that transfer
across environments:

- **storage slots written per round** — exact, deterministic, no chain required;
- **gas units per round** — portable.

Wei is deliberately absent. Gas price on Robinhood Chain moved by a factor of ~4.6 within ten
days (see below), so any wei figure is a snapshot of a moving target, and ArbOS's own gas
accounting does not exist in a local EVM, so a wei figure computed locally would be a local
artefact. Local gas units are a **lower bound** on the on-chain figure: they exclude ArbOS's
component.

## Slots written per round

Measured with `vm.accesses` over a commit on a fresh ledger (`test/Gas.t.sol`):

| rounds in batch | slots written |
|---|---|
| 1 | 3 |
| 2 | 5 |
| 8 | 17 |
| 33 | 67 |

**Two slots per round, plus one watermark slot per batch** — writes = `2N + 1`, exact.

The layout is the naive one and cannot be improved without changing the record: `bytes32`
fills a slot on its own, so `canon` cannot share it. The only thing batch size amortises is
the single watermark slot, which is why the per-round figure is essentially constant below.

`spec-v1.0.md` §3 has carried "naive layout, two slots per round" as an estimate since
2026-09-12. This measurement settles it: the estimate was right, and it is now a measurement
rather than a guess.

## Gas units per round

`test/Gas.t.sol`, local EVM, solc 0.8.36, `evm_version = paris`, optimizer on:

> Per-batch totals move by a few tens of gas when unrelated bytecode changes (a layout
> shift, an added constructor check), so treat the individual totals as indicative. The
> **marginal** figures below have been stable across every change so far, and they are the
> ones the batch-size decision rests on.

| rounds in batch | gas |
|---|---|
| 1 | 95,207 |
| 8 | 414,736 |
| 33 | 1,555,955 |
| 129 | 5,938,874 |

Marginal cost per additional round, measured at three spans rather than extrapolated:

| span | marginal gas/round |
|---|---|
| 1 → 8 | 45,647 |
| 8 → 33 | 45,648 |
| 33 → 129 | 45,655 |

**≈45,650 gas per round**, flat across two orders of magnitude of batch size.
Including per-batch overhead: 47,150 gas/round at a batch of 33, 46,037 at 129.

The two slot writes account for 40,000 of that. The remaining ~5,650 is call overhead and
argument decoding — worth naming so that nobody re-derives "40,000 gas per round" from the
slot count and concludes this measurement is wrong.

## What this means for batch size

Not gas, and not calldata size. At ~45,650 gas/round a 460-round backfill is ~21M gas, and a
129-round batch is ~5.9M — both far below any block limit, and the block gas limit on this
chain is `2^50` (`1,125,899,906,842,624`), Arbitrum's "no practical limit" sentinel. Calldata
is 64 bytes per round: 8,192 bytes for 129 rounds, an order of magnitude away from any
sequencer limit.

The binding constraint is the **failure radius**. A batch is all-or-nothing (`commitBatch`
validates the whole array before moving the watermark; a revert writes nothing — asserted in
`test/EvidenceLedger.t.sol`). A failed 460-round batch costs all 460 rounds of work. So batch
size should be chosen by how much work is acceptable to lose and redo, not by what fits.

## Gas price on this chain is not a constant, and calldata is not free

Both read from `ArbGasInfo.getPricesInWei()` at historical blocks. Recorded here because both
were at some point assumed to be stable, and neither is.

**`perStorageAllocation` is not an independent charge.** Six blocks:

| block | date | perL1CalldataByte | perStorageAllocation | perArbGasTotal | ratio |
|---|---|---|---|---|---|
| 53,983,886 | 09-04 04:33 | 0 | 6,574,160,000,000 | 328,708,000 | 20,000 |
| 55,165,566 | 09-05 13:43 | 0 | 7,635,640,000,000 | 381,782,000 | 20,000 |
| 57,000,000 | 09-07 17:07 | 0 | 6,206,000,000,000 | 310,300,000 | 20,000 |
| 59,000,000 | 09-10 01:10 | 25,672,560 | 3,250,080,000,000 | 162,504,000 | 20,000 |
| 61,129,566 | 09-12 13:07 | 0 | 1,859,800,000,000 | 92,990,000 | 20,000 |
| 62,408,402 | 09-14 01:12 | 0 | 1,656,600,000,000 | 82,830,000 | 20,000 |

`perStorageAllocation = perArbGasTotal × 20,000` in every row. It is the cost of a new
`SSTORE` slot (20,000 gas) expressed in wei — **not an ArbOS surcharge on top of EVM gas**.
Storage allocation *is* the opcode. `perArbGasTotal` moved 4.6× in ten days
(381,782,000 → 82,830,000).

**`perL1CalldataByte = 0` is not structural.** Sampling across the same window:

| block | date | perL1CalldataByte |
|---|---|---|
| 58,800,000 | 09-09 19:34 | 0 |
| 58,900,000 | 09-09 22:22 | 0 |
| 58,950,000 | 09-09 23:46 | 41,059,664 |
| 59,050,000 | 09-10 02:34 | 64,706,688 |
| 59,300,000 | 09-10 09:34 | 85,625,520 |
| 60,000,000 | 09-11 05:12 | 73,240,928 |
| 61,129,566 | 09-12 13:07 | 0 |

It charged a non-zero rate for more than a day. It oscillates between zero and tens of
millions of wei per byte. **"Calldata is free" must not appear in any design assumption.**
An earlier reading of a single block was mistaken for a property; that is recorded here so
the same inference is not made again.

## What the 46630 run is for

Not for per-round gas — that is fixed above and does not need a chain. The testnet run exists
to measure what a local EVM cannot:

1. how much ArbOS adds to the local gas figure, and how `perL1CalldataByte` behaves during it;
2. that the slot count matches (2 per round), i.e. that the local layout is the deployed layout;
3. the real cost at real prices, as a snapshot with its block number attached.

## What the two runs measured (answered 2026-09-26)

The section above lists three things a local EVM cannot measure. Two runs happened — testnet
2026-09-19, mainnet 2026-09-23 — and each answer says which chain it comes from and what it was
measured from. **The two chains are not compared with each other**: the runs are in different
windows, and `perL1CalldataByte` moved inside a single window (the table above: 0 → 41,059,664 →
85,625,520 → 0).

### 1. What ArbOS adds to the local figure

| run | rounds | transactions | total gas | gas per round | vs local 45,650 |
|---|---|---|---|---|---|
| testnet 46630, 09-19 | 610 | 10 | 29,012,366 | 47,561.3 | **+1,911.3 (+4.2%)** |
| mainnet 4663, 09-23 | 850 | 14 | 39,113,313 | 46,015.7 | **+365.7 (+0.8%)** |

Measured from the receipts under `broadcast/Commit.s.sol/<chain>/`, with the rehearsal artifacts
(`broadcast/_rehearsal/`) excluded — and the chain confirms that exclusion independently: each
ledger carries exactly one `OwnershipTransferred` followed by 14 (mainnet) / 10 (testnet)
`BatchCommitted` events, so no unarchived real run exists.

Rounds per transaction are **not** inferred from gas. The `BatchCommitted` events carry the count
and decode to 13×64 + 18 = 850 and 9×64 + 34 = 610; an earlier pass had estimated the tail batches
at 18 and 34 from the last transaction's gas, which the events then confirmed exactly.

### 2. The deployed storage layout is the local one

Read with `eth_getStorageAt` on both ledgers:

```
slot 0  owner                                                       == the owner published on the report page
slot 1  pendingOwner (low 20 bytes) + lastCommittedBlock (next 8)    == 0 and 64,907,249 / 69,196,861
slot 2  records mapping head (empty)
```

`address(20B) + uint64(8B) = 28B`, so Solidity packs those two into one slot. Each
`CommitRecord{bytes32 roundHash; uint8 canon}` occupies two slots, which is the "2 slots per
round" the gas figure assumes, and 47,561 gas per round is consistent with two cold SSTOREs plus
overhead.

Two errors of mine are recorded here, because both are the error class this repository keeps
finding — measuring a boundary one imposed and reading it as a property of the object. The first
version printed only the low 20 bytes of slot 1, read zero there, and announced that the deployed
layout disagreed with the local one; the zero was produced by my own truncation, exactly like the
60-character revert message in the collector. The second printed the whole slot but sliced the
wrong eight bytes. The checker also reported an HTTP 403 from the RPC as "the layout disagrees",
merging a refusal with an answer; it now exits 3 and draws no conclusion.

### 3. Cost at real prices, with the block numbers

| run | transactions | total gas | gas price | cost | per round |
|---|---|---|---|---|---|
| testnet 46630 | 10 | 29,012,366 | 10,000,000 wei (uniform) | **0.000290123660 ETH** | 0.4756 µETH |
| mainnet 4663 | 14 | 39,113,313 | 56,122,000 .. 57,880,000 wei | **0.002222287992 ETH** | 2.6145 µETH |

Cost is the sum of `gasUsed × effectiveGasPrice` per transaction, not total gas times an average
price: each of the 14 mainnet transactions paid a different price, and an average would produce a
figure nobody can recompute. The testnet figure agrees with the 0.00029 ETH recorded when the
backfill was broadcast.

```
testnet  blocks 0x73f29e3 .. 0x73f2a80   = 121,580,003 .. 121,580,160   (10 transactions)
mainnet  blocks 0x42ecf4e .. 0x42ed001   =  70,176,590 ..  70,176,769   (14 transactions)
```

### 4. `perL1CalldataByte` during the runs

* **mainnet 4663 — measured.** 14/14 transaction blocks return **0**: `0x42ecf4e · 0x42ecf5c ·
  0x42ecf69 · 0x42ecf78 · 0x42ecf85 · 0x42ecf94 · 0x42ecfa1 · 0x42ecfaf · 0x42ecfbd · 0x42ecfca ·
  0x42ecfd7 · 0x42ecfe5 · 0x42ecff3 · 0x42ed001`. Over the same blocks `perArbGasTotal` was
  56,122,000 .. 57,880,000 wei and `perStorageAllocation = perArbGasTotal × 20,000` held on 14/14.
* **testnet 46630 — open, and not measurable from here.** The public endpoint refuses historical
  state at 121,580,003 and 121,580,160 (it answers for `latest`; the refusal names the state
  root), and that chain has no archive endpoint. It stays open until the next real commit can be
  sampled.

**Zero in one window is not "free".** The table above records this rate oscillating between zero
and 85,625,520 wei per byte inside a single window; this section exists partly so that a zero
measured once is not read as a property of the chain.

## Reproducing

```sh
forge test --match-contract GasTest -vv
```
