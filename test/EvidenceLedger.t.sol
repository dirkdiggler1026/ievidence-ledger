// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

import {Test} from "forge-std/Test.sol";
import {EvidenceLedger} from "../src/EvidenceLedger.sol";
import {IEvidenceLedger} from "../src/IEvidenceLedger.sol";

/// @notice First test set for the frozen interface (spec v1.0 搂3, 搂4, 搂5).
///         Covers: commit semantics, the monotonic watermark, read semantics for recorded
///         and absent rounds, the batch event, and both steps of the ownership transfer.
contract EvidenceLedgerTest is Test {
    EvidenceLedger internal ledger;

    address internal constant OWNER = address(0xA11CE);
    address internal constant STRANGER = address(0xB0B);
    address internal constant NEW_OWNER = address(0xCAFE);

    uint8 internal constant CANON = 2; // rhdepth-v2

    // Real values from the measurement line, so the tests exercise the shapes that matter.
    uint64 internal constant BLOCK_A = 54_088_399; // first v2 round
    uint64 internal constant BLOCK_B = 54_662_240; // last round of 2026-09-04
    bytes32 internal constant HASH_A =
        0x2c645bebf891401f1fd75a39168496a88a45bfed59105c42f780f5b65421a2d2;
    bytes32 internal constant HASH_B =
        0xd80bbe440f376893c727e10983cec81a6100943f798a23aaf4d23aa7dae8ee1c;

    function setUp() public {
        vm.prank(OWNER);
        ledger = new EvidenceLedger(CANON);
    }

    function _batch(uint64 b0, bytes32 h0, uint64 b1, bytes32 h1)
        internal
        pure
        returns (uint64[] memory blocks, bytes32[] memory hashes)
    {
        blocks = new uint64[](2);
        hashes = new bytes32[](2);
        blocks[0] = b0;
        hashes[0] = h0;
        blocks[1] = b1;
        hashes[1] = h1;
    }

    // --- construction ---------------------------------------------------------------

    function test_ConstructorSetsOwnerAndCanon() public view {
        assertEq(ledger.owner(), OWNER, "owner");
        assertEq(ledger.CANON(), CANON, "canon");
        assertEq(ledger.latestCommittedBlock(), 0, "watermark starts at zero");
        assertEq(ledger.pendingOwner(), address(0), "no transfer pending");
    }

    /// Zero is the value the absent-round signal occupies, so a deployment stamping zero would
    /// make every recorded round read as never committed -- with a successful deployment,
    /// successful commits, and nothing anywhere reporting a problem. The interface documents
    /// that inference, so the contract has to be what makes it true rather than a script on
    /// someone's machine.
    function test_Constructor_RejectsZeroCanon() public {
        vm.prank(OWNER);
        vm.expectRevert(abi.encodeWithSelector(EvidenceLedger.InvalidCanon.selector, 0));
        new EvidenceLedger(0);
    }

    /// The bound is `== 0`, not `< 2`: only the absence value is refused, so a future canon
    /// number stays a deploy-time choice rather than a contract change.
    function test_Constructor_AcceptsOtherCanons() public {
        vm.prank(OWNER);
        EvidenceLedger other = new EvidenceLedger(3);
        assertEq(other.CANON(), 3, "canon 3 is a valid deployment");
    }

    // --- commit ---------------------------------------------------------------------

    function test_CommitBatch_WritesRecordsAndMovesWatermark() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_B, HASH_B);

        vm.prank(OWNER);
        ledger.commitBatch(blocks, hashes);

        (bytes32 h0, uint8 c0) = ledger.getRoundHash(BLOCK_A);
        (bytes32 h1, uint8 c1) = ledger.getRoundHash(BLOCK_B);
        assertEq(h0, HASH_A, "hash A");
        assertEq(h1, HASH_B, "hash B");
        assertEq(c0, CANON, "canon A");
        assertEq(c1, CANON, "canon B");
        assertEq(ledger.latestCommittedBlock(), BLOCK_B, "watermark is the last block");
        assertEq(ledger.latestCommittedBlock(), BLOCK_B, "latestCommittedBlock agrees");
    }

    function test_CommitBatch_EmitsRangeAndCount() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_B, HASH_B);

        vm.expectEmit(true, true, false, true, address(ledger));
        emit IEvidenceLedger.BatchCommitted(BLOCK_A, BLOCK_B, 2);

        vm.prank(OWNER);
        ledger.commitBatch(blocks, hashes);
    }

    function test_ConsecutiveBatches_ContinueIncreasing() public {
        (uint64[] memory b1, bytes32[] memory h1) = _batch(BLOCK_A, HASH_A, BLOCK_B, HASH_B);
        vm.prank(OWNER);
        ledger.commitBatch(b1, h1);

        (uint64[] memory b2, bytes32[] memory h2) = _batch(BLOCK_B + 1, HASH_A, BLOCK_B + 2, HASH_B);
        vm.prank(OWNER);
        ledger.commitBatch(b2, h2);

        assertEq(ledger.latestCommittedBlock(), BLOCK_B + 2, "watermark follows the second batch");
    }

    // --- absent rounds --------------------------------------------------------------

    function test_GetRoundHash_AbsentRoundIsZeroAndCanonZero() public view {
        (bytes32 h, uint8 c) = ledger.getRoundHash(BLOCK_A);
        assertEq(h, bytes32(0), "no hash");
        // canon == 0 is the "never recorded" signal: recorded rounds always carry canon >= 2.
        assertEq(c, 0, "canon zero marks absent");
    }

    // --- access control -------------------------------------------------------------

    function test_CommitBatch_RevertsForNonOwner() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_B, HASH_B);

        vm.prank(STRANGER);
        vm.expectRevert(abi.encodeWithSelector(EvidenceLedger.NotOwner.selector, STRANGER));
        ledger.commitBatch(blocks, hashes);

        assertEq(ledger.latestCommittedBlock(), 0, "nothing written");
    }

    // --- input constraints (the two the spec states) --------------------------------

    function test_CommitBatch_RevertsOnLengthMismatch() public {
        uint64[] memory blocks = new uint64[](2);
        bytes32[] memory hashes = new bytes32[](1);
        blocks[0] = BLOCK_A;
        blocks[1] = BLOCK_B;
        hashes[0] = HASH_A;

        vm.prank(OWNER);
        vm.expectRevert(abi.encodeWithSelector(EvidenceLedger.LengthMismatch.selector, 2, 1));
        ledger.commitBatch(blocks, hashes);
    }

    function test_CommitBatch_RevertsOnDuplicateBlock() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_A, HASH_B);

        vm.prank(OWNER);
        vm.expectRevert(
            abi.encodeWithSelector(EvidenceLedger.BlocksNotIncreasing.selector, BLOCK_A, BLOCK_A)
        );
        ledger.commitBatch(blocks, hashes);
    }

    function test_CommitBatch_RevertsOnDescendingBlocks() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_B, HASH_A, BLOCK_A, HASH_B);

        vm.prank(OWNER);
        vm.expectRevert(
            abi.encodeWithSelector(EvidenceLedger.BlocksNotIncreasing.selector, BLOCK_B, BLOCK_A)
        );
        ledger.commitBatch(blocks, hashes);
    }

    /// Re-running a backfill must be idempotent rather than duplicating: a batch whose first
    /// block is at or below the watermark reverts (spec 搂6 "骞傜瓑").
    function test_ReplayOfCommittedBatch_Reverts() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_B, HASH_B);
        vm.prank(OWNER);
        ledger.commitBatch(blocks, hashes);

        vm.prank(OWNER);
        vm.expectRevert(
            abi.encodeWithSelector(EvidenceLedger.BlocksNotIncreasing.selector, BLOCK_B, BLOCK_A)
        );
        ledger.commitBatch(blocks, hashes);
    }

    /// A failed batch must leave no partial state: the watermark only moves after the whole
    /// batch validates.
    function test_FailedBatch_LeavesNoPartialState() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_A, HASH_B);

        vm.prank(OWNER);
        vm.expectRevert();
        ledger.commitBatch(blocks, hashes);

        (bytes32 h, uint8 c) = ledger.getRoundHash(BLOCK_A);
        assertEq(h, bytes32(0), "no record written");
        assertEq(c, 0, "no canon written");
        assertEq(ledger.latestCommittedBlock(), 0, "watermark untouched");
    }

    function test_EmptyBatch_IsNoOp() public {
        uint64[] memory blocks = new uint64[](0);
        bytes32[] memory hashes = new bytes32[](0);

        vm.prank(OWNER);
        ledger.commitBatch(blocks, hashes); // must not revert

        assertEq(ledger.latestCommittedBlock(), 0, "watermark untouched");
    }

    /// "Harmless no-op" has to mean harmless on the wire too, not just in storage: an empty
    /// batch must not put a `BatchCommitted` into the log stream, because the watchdog (D9)
    /// and any indexer read that stream. Asserting the absence is cheaper than adding a
    /// constraint to the frozen interface, and it pins the property that actually matters.
    function test_EmptyBatch_EmitsNoEvent() public {
        uint64[] memory blocks = new uint64[](0);
        bytes32[] memory hashes = new bytes32[](0);

        vm.recordLogs();
        vm.prank(OWNER);
        ledger.commitBatch(blocks, hashes);

        assertEq(vm.getRecordedLogs().length, 0, "an empty batch must not emit");
        assertEq(ledger.latestCommittedBlock(), 0, "watermark untouched");
        assertEq(ledger.pendingOwner(), address(0), "nothing else changed either");
    }

    /// Same property for a batch that reverts: no log may survive a failed call.
    function test_FailedBatch_EmitsNoEvent() public {
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_A, HASH_B);

        vm.recordLogs();
        vm.prank(OWNER);
        vm.expectRevert();
        ledger.commitBatch(blocks, hashes);

        assertEq(vm.getRecordedLogs().length, 0, "a reverted batch must not emit");
    }

    // --- two-step ownership (decision B7) -------------------------------------------

    function test_TransferOwnership_RequiresAcceptance() public {
        vm.prank(OWNER);
        ledger.transferOwnership(NEW_OWNER);
        assertEq(ledger.owner(), OWNER, "owner unchanged until accepted");
        assertEq(ledger.pendingOwner(), NEW_OWNER, "pending owner set");

        vm.prank(NEW_OWNER);
        ledger.acceptOwnership();
        assertEq(ledger.owner(), NEW_OWNER, "owner is the new owner");
        assertEq(ledger.pendingOwner(), address(0), "pending cleared");
    }

    function test_AcceptOwnership_RevertsForNonPendingOwner() public {
        vm.prank(OWNER);
        ledger.transferOwnership(NEW_OWNER);

        vm.prank(STRANGER);
        vm.expectRevert(abi.encodeWithSelector(EvidenceLedger.NotPendingOwner.selector, STRANGER));
        ledger.acceptOwnership();
        assertEq(ledger.owner(), OWNER, "owner unchanged");
    }

    function test_TransferOwnership_RevertsOnZeroAddress() public {
        vm.prank(OWNER);
        vm.expectRevert(EvidenceLedger.ZeroAddress.selector);
        ledger.transferOwnership(address(0));
    }

    function test_TransferOwnership_RevertsForNonOwner() public {
        vm.prank(STRANGER);
        vm.expectRevert(abi.encodeWithSelector(EvidenceLedger.NotOwner.selector, STRANGER));
        ledger.transferOwnership(STRANGER);
    }

    /// After a completed transfer the old owner can no longer commit 鈥?the write path
    /// followed the transfer, which is the only reason to have one.
    function test_AfterTransfer_OldOwnerCannotCommit() public {
        vm.prank(OWNER);
        ledger.transferOwnership(NEW_OWNER);
        vm.prank(NEW_OWNER);
        ledger.acceptOwnership();

        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(BLOCK_A, HASH_A, BLOCK_B, HASH_B);

        vm.prank(OWNER);
        vm.expectRevert(abi.encodeWithSelector(EvidenceLedger.NotOwner.selector, OWNER));
        ledger.commitBatch(blocks, hashes);

        vm.prank(NEW_OWNER);
        ledger.commitBatch(blocks, hashes);
        assertEq(ledger.latestCommittedBlock(), BLOCK_B, "new owner can commit");
    }

    // --- fuzz ---------------------------------------------------------------------

    function testFuzz_CommitBatch_AcceptsOnlyIncreasing(uint64 first, uint64 second) public {
        vm.assume(first > 0 && first < type(uint64).max - 1);
        vm.assume(second > first);

        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(first, HASH_A, second, HASH_B);
        vm.prank(OWNER);
        ledger.commitBatch(blocks, hashes);

        assertEq(ledger.latestCommittedBlock(), second, "watermark");
        (bytes32 h, uint8 c) = ledger.getRoundHash(first);
        assertEq(h, HASH_A, "first hash");
        assertEq(c, CANON, "first canon");
    }

    function testFuzz_CommitBatch_RevertsWhenNotIncreasing(uint64 first, uint64 second) public {
        vm.assume(first > 0);
        vm.assume(second <= first);

        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(first, HASH_A, second, HASH_B);
        vm.prank(OWNER);
        vm.expectRevert();
        ledger.commitBatch(blocks, hashes);
        assertEq(ledger.latestCommittedBlock(), 0, "watermark untouched");
    }
}
