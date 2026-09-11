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

| file | what it is |
|---|---|
| `spec-v1.0.md` | contract interface, canonical serialization, anchor model, demo scope |
| `SPEC-DECISIONS.md` | the decision log behind the spec, including what was rejected and why |

## Anchor model

The ledger writes one hash per measurement round, derived only from quantities a third
party can reproduce at the same block height. It stores no measurement data: the data
stays in the repository above, and the ledger is the notary, not the warehouse. See
`spec-v1.0.md` §2, §3 and §5.

## License

Code: Apache License 2.0 (`LICENSE`). Specification and documents: CC BY 4.0. See `NOTICE`.

## Contact

dirkdiggler871026@gmail.com
