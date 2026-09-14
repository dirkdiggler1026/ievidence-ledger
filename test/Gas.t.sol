// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

import {Test} from "forge-std/Test.sol";
import {EvidenceLedger} from "../src/EvidenceLedger.sol";

/// @notice Portable cost measurements. Deliberately measures only the two quantities that
///         transfer across environments: **storage slots written per round** (exact and
///         deterministic) and **gas units per round** (portable). Wei is not measured here
///         and must not be derived from this file — gas price on Robinhood Chain moved by a
///         factor of ~4.6 within ten days, and ArbOS gas accounting does not exist in a local
///         EVM, so any wei figure computed here would be a local artefact.
///         Local gas units do not include ArbOS's own component, so this is a lower bound on
///         the on-chain figure; the 46630 run exists to measure that difference, not to
///         measure per-round gas, which is fixed here.
contract GasTest is Test {
    uint8 internal constant CANON = 2;
    uint64 internal constant FIRST_BLOCK = 54_088_399;

    address internal constant OWNER = address(0xA11CE);

    function _batch(uint256 n)
        internal
        pure
        returns (uint64[] memory blocks, bytes32[] memory hashes)
    {
        blocks = new uint64[](n);
        hashes = new bytes32[](n);
        for (uint256 i = 0; i < n; ++i) {
            blocks[i] = FIRST_BLOCK + uint64(i * 18_000); // ~30 min apart, as in production
            hashes[i] = keccak256(abi.encodePacked("round", i));
        }
    }

    function _fresh() internal returns (EvidenceLedger) {
        vm.prank(OWNER);
        return new EvidenceLedger(CANON);
    }

    // --- slots: exact, deterministic, no chain needed ---------------------------------

    /// @dev `vm.accesses` reports the distinct slots touched, so an N-round batch on a fresh
    ///      ledger should write exactly 2N + 1 slots: two per record (roundHash and canon
    ///      cannot share a 32-byte slot) plus the watermark, written once per batch.
    function _slotsWritten(uint256 n) internal returns (uint256) {
        EvidenceLedger ledger = _fresh();
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(n);

        vm.record();
        vm.prank(OWNER);
        ledger.commitBatch(blocks, hashes);
        (, bytes32[] memory writes) = vm.accesses(address(ledger));
        return writes.length;
    }

    function test_SlotsPerRound_SingleRound() public {
        uint256 slots = _slotsWritten(1);
        emit log_named_uint("slots written, 1 round  (2 record + 1 watermark)", slots);
        assertEq(slots, 3, "1 round = 2 record slots + 1 watermark slot");
    }

    function test_SlotsPerRound_TwoRounds() public {
        uint256 slots = _slotsWritten(2);
        emit log_named_uint("slots written, 2 rounds", slots);
        assertEq(slots, 5, "2 rounds = 4 record slots + 1 watermark slot");
    }

    function test_SlotsPerRound_EightRounds() public {
        uint256 slots = _slotsWritten(8);
        emit log_named_uint("slots written, 8 rounds", slots);
        assertEq(slots, 17, "8 rounds = 16 record slots + 1 watermark slot");
    }

    /// The spec has carried "naive layout, two slots per round" as an estimate since
    /// 2026-09-12. This is the measurement that settles it: two slots per round is exact,
    /// and the only thing batch size amortises is the single watermark slot.
    function test_SlotsPerRound_Formula() public {
        assertEq(_slotsWritten(1), 3, "N=1");
        assertEq(_slotsWritten(2), 5, "N=2");
        assertEq(_slotsWritten(8), 17, "N=8");
        assertEq(_slotsWritten(33), 67, "N=33");
    }

    // --- gas: portable across environments --------------------------------------------

    function _gasFor(uint256 n) internal returns (uint256) {
        EvidenceLedger ledger = _fresh();
        (uint64[] memory blocks, bytes32[] memory hashes) = _batch(n);

        vm.prank(OWNER);
        uint256 before = gasleft();
        ledger.commitBatch(blocks, hashes);
        return before - gasleft();
    }

    function test_GasPerRound_Table() public {
        uint256 g1 = _gasFor(1);
        uint256 g8 = _gasFor(8);
        uint256 g33 = _gasFor(33);
        uint256 g129 = _gasFor(129);

        emit log_named_uint("gas, batch of 1", g1);
        emit log_named_uint("gas, batch of 8", g8);
        emit log_named_uint("gas, batch of 33", g33);
        emit log_named_uint("gas, batch of 129", g129);

        // Marginal cost per additional round, measured rather than assumed.
        emit log_named_uint("marginal gas/round, 1->8", (g8 - g1) / 7);
        emit log_named_uint("marginal gas/round, 8->33", (g33 - g8) / 25);
        emit log_named_uint("marginal gas/round, 33->129", (g129 - g33) / 96);
        emit log_named_uint("gas/round incl. overhead, batch of 33", g33 / 33);
        emit log_named_uint("gas/round incl. overhead, batch of 129", g129 / 129);
    }

    /// A batch of 129 is not special — it is the largest single batch the backfill is
    /// expected to use, so it is the one worth having a number for.
    function test_Gas_BatchOf129FitsComfortably() public {
        uint256 used = _gasFor(129);
        // 129 rounds is ~2 slot-writes per round plus overhead, measured at ~5.94M.
        // The ceiling is set with headroom so it fails on a material regression, not on noise.
        assertLt(used, 7_500_000, "129 rounds should stay well under 7.5M gas");
    }
}
