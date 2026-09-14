// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

import {IEvidenceLedger} from "./IEvidenceLedger.sol";

/// @title EvidenceLedger
/// @notice Implementation of the frozen spec v1.0 §3 interface. See `IEvidenceLedger` for
///         the semantics and the pointers into the spec.
///
/// Scope discipline: this contract implements exactly the constraints the spec states
/// (equal-length arrays, strictly increasing blocks) and nothing beyond them. Guards that
/// would *look* prudent — rejecting a zero `roundHash`, rejecting an empty batch — were
/// deliberately not added, because the spec is frozen on 2026-09-13 and a new constraint is
/// a design change, not an implementation detail. Neither is needed for correctness:
/// a zero hash is distinguishable from "not recorded" because `canon` is 0 only when absent,
/// and an empty batch is a no-op that emits nothing.
contract EvidenceLedger is IEvidenceLedger {
    /// @notice Canon version stamped on every round this deployment accepts. All rounds in
    ///         the v1.0 backfill are canon 2 (rhdepth-v2).
    /// @dev    Held per record rather than globally so that a future canon upgrade cannot
    ///         retroactively relabel rounds that were committed under an older canon
    ///         (spec §7: "historical rounds keep their original canon").
    ///         Note the open question this does not resolve: the frozen `commitBatch`
    ///         signature carries no canon argument, so "which canon" is fixed at deployment.
    ///         A canon upgrade therefore needs either a new ledger or a spec change.
    ///         That is a spec-level decision; it is recorded in the repo, not decided here.
    uint8 public immutable CANON;

    /// @notice The only address allowed to commit.
    address public owner;

    /// @notice Address that may complete a two-step ownership transfer; zero if none pending.
    address public pendingOwner;

    /// @notice Strictly increasing watermark.
    /// @dev    `internal` on purpose (decision B12). The first frozen draft declared it
    ///         `public`, which generated a second entry point — `lastCommittedBlock()` —
    ///         alongside the `latestCommittedBlock()` that spec §5 and decision B5 actually
    ///         name. Two entry points to one fact, both returning `uint64`, both plausible,
    ///         nothing in the ABI to tell them apart, and a consumer left to guess; a guess
    ///         that later diverged would fail silently. The getter was an artefact of the
    ///         visibility keyword rather than a design decision, so it is the one that goes.
    ///         `latestCommittedBlock()` below is the read interface.
    uint64 internal lastCommittedBlock;

    /// @dev The source of truth for round hashes. Named `records`, not `committed`, on
    ///      purpose: `committed` is the name of a field in the published data files that is
    ///      permanently `false` (spec §5). Same word, opposite meaning, is an error class in
    ///      this project, so the two do not share a name.
    mapping(uint64 blockRef => CommitRecord) internal records;

    error NotOwner(address caller);
    error NotPendingOwner(address caller);
    error ZeroAddress();
    error LengthMismatch(uint256 blocksLength, uint256 hashesLength);
    error BlocksNotIncreasing(uint64 previous, uint64 next);
    error InvalidCanon(uint8 canon);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner(msg.sender);
        _;
    }

    /// @param canon_ Canon version stamped on every round this deployment accepts.
    ///               Pass 2 for rhdepth-v2. Zero is refused.
    /// @dev    The refusal is what makes "canon == 0 means never committed" a property of the
    ///         contract rather than of whoever deployed it. `IEvidenceLedger` documents that
    ///         inference; without this check a deployment with `canon_ = 0` would stamp 0 on
    ///         every record, the read interface would report every round as absent, and nothing
    ///         along the way would fail -- deploy succeeds, commits succeed, tests pass, and the
    ///         guarantee the interface states would simply be false for that deployment.
    ///         The guarantee belongs where it is declared, not in a deploy script on someone's
    ///         laptop: a third party reading this contract on Blockscout should be able to see
    ///         it. The bound is `== 0` and not `< 2` deliberately: zero is the value the absent
    ///         signal occupies, which is the minimum this invariant needs. Anything stricter
    ///         would weld spec §7's version table into the contract.
    constructor(uint8 canon_) {
        if (canon_ == 0) revert InvalidCanon(canon_);
        owner = msg.sender;
        CANON = canon_;
        emit OwnershipTransferred(address(0), msg.sender);
    }

    /// @inheritdoc IEvidenceLedger
    function commitBatch(uint64[] calldata blocks, bytes32[] calldata roundHashes)
        external
        onlyOwner
    {
        uint256 n = blocks.length;
        if (n != roundHashes.length) revert LengthMismatch(n, roundHashes.length);

        // Read the watermark once, compare against the running value inside the loop.
        // Every block must exceed the previously accepted one, so a batch cannot contain a
        // duplicate and cannot reach backwards past the watermark.
        uint64 previous = lastCommittedBlock;
        for (uint256 i = 0; i < n; ++i) {
            uint64 blockRef = blocks[i];
            if (blockRef <= previous) revert BlocksNotIncreasing(previous, blockRef);
            records[blockRef] = CommitRecord({roundHash: roundHashes[i], canon: CANON});
            previous = blockRef;
        }

        // Empty batches are accepted as no-ops and do not move the watermark or emit.
        if (n == 0) return;

        lastCommittedBlock = previous;
        emit BatchCommitted(blocks[0], previous, n);
    }

    /// @inheritdoc IEvidenceLedger
    function getRoundHash(uint64 blockRef) external view returns (bytes32, uint8) {
        CommitRecord storage record = records[blockRef];
        return (record.roundHash, record.canon);
    }

    /// @inheritdoc IEvidenceLedger
    function latestCommittedBlock() external view returns (uint64) {
        return lastCommittedBlock;
    }

    /// @inheritdoc IEvidenceLedger
    function transferOwnership(address newOwner) external onlyOwner {
        // Zero is rejected because the whole point of the two-step transfer is to protect
        // against a mistyped destination; address(0) is the one mistake that can never be
        // accepted, so it is refused at the start rather than at acceptance time.
        if (newOwner == address(0)) revert ZeroAddress();
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    /// @inheritdoc IEvidenceLedger
    function acceptOwnership() external {
        if (msg.sender != pendingOwner) revert NotPendingOwner(msg.sender);
        address previous = owner;
        owner = msg.sender;
        pendingOwner = address(0);
        emit OwnershipTransferred(previous, msg.sender);
    }
}
