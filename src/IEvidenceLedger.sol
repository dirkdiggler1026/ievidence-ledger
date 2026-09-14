// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

/// @title IEvidenceLedger
/// @notice The notary layer for the executable-depth measurement line. Frozen interface,
///         spec v1.0 §3. The chain answers "was this round recorded, and what is its
///         fingerprint" — it does not answer "what was the price" (spec §9).
///
/// Key design point (spec §3): round hashes live in storage, not only in an event.
/// `getRoundHash` is a read-only state function and cannot read logs; dropping the read
/// interface would drop the "future consumable" surface. Events are emitted too, for
/// indexing and display, but they are not the source of truth.
interface IEvidenceLedger {
    /// @param roundHash The round's roundKeccak (canonical serialisation, spec §2.2).
    /// @param canon     Canon version of that round. 2 = rhdepth-v2 (spec §7).
    ///                  canon == 0 means the block was never recorded, because recorded
    ///                  rounds always carry canon >= 2.
    struct CommitRecord {
        bytes32 roundHash;
        uint8 canon;
    }

    /// @notice Emitted once per `commitBatch` call. The watchdog (D9) subscribes to this:
    ///         any batch we did not send means the append path is compromised.
    event BatchCommitted(uint64 indexed firstBlock, uint64 indexed lastBlock, uint256 count);

    /// @notice Emitted when a two-step ownership transfer is started. `owner` is unchanged
    ///         until the pending owner accepts.
    event OwnershipTransferStarted(address indexed currentOwner, address indexed pendingOwner);

    /// @notice Emitted when a two-step ownership transfer completes.
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    /// @notice Record one hash per round. Owner-only.
    /// @dev    Constraints (spec §3, §4.2): the two arrays are equal length, and `blocks`
    ///         is strictly increasing. Strict increase against `lastCommittedBlock` gives
    ///         deduplication and append-only ordering with a single monotonic watermark —
    ///         no separate revocation semantics are needed or provided.
    ///         Re-running a backfill is therefore idempotent: already-committed blocks revert.
    function commitBatch(uint64[] calldata blocks, bytes32[] calldata roundHashes) external;

    /// @notice The recorded hash and canon for a round.
    /// @return roundHash The roundKeccak, or bytes32(0) if never recorded.
    /// @return canon     The canon version, or 0 if never recorded.
    function getRoundHash(uint64 blockRef) external view returns (bytes32 roundHash, uint8 canon);

    /// @notice The highest block ever committed. One call answers "which rounds are on chain"
    ///         (spec §5).
    /// @dev    This is the only name for the watermark in the ABI. An earlier version also
    ///         exposed `lastCommittedBlock()` — the automatic getter of a `public` state
    ///         variable. That was the same fact behind two entry points, both returning
    ///         `uint64`, both looking authoritative, with nothing in the ABI to say which one
    ///         a consumer should read. A consumer had to guess, and a guess that later diverged
    ///         would fail silently. Spec §5 and decision B5 both name this function; the
    ///         getter was a Solidity by-product, so it is the one that goes (decision B12).
    function latestCommittedBlock() external view returns (uint64);

    /// @notice Start a two-step ownership transfer (spec §3, decision B7).
    /// @dev    The pending owner must call `acceptOwnership`. Without the second step a
    ///         mistyped address would permanently brick the only write path. Honest boundary:
    ///         this only rescues the window between "key known to be compromised" and "the
    ///         attacker has used it". It does not undo a commit already made.
    function transferOwnership(address newOwner) external;

    /// @notice Complete a two-step ownership transfer. Callable only by the pending owner.
    function acceptOwnership() external;

    /// @notice Current owner; the only address allowed to commit.
    function owner() external view returns (address);

    /// @notice Address that may accept ownership, or zero if no transfer is pending.
    function pendingOwner() external view returns (address);
}
