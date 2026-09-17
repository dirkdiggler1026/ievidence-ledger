// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

import {Script, console2} from "forge-std/Script.sol";
import {EvidenceLedger} from "../src/EvidenceLedger.sol";

/// @title Commit a frozen round list to a deployed ledger, in batches
/// @notice `commitBatch` advances a monotonic watermark that cannot be moved backwards, so a
///         wrong row order or a wrong hash is not a failure that can be retried away -- it
///         permanently poisons the ledger, and on mainnet the artefact third parties verify.
///         Every gate below runs before the first transaction, and the two gates the contract
///         cannot provide are the two that matter most.
///
/// Required environment:
///
///   LEDGER_CHAIN_ID  4663 or 46630, asserted against block.chainid
///   LEDGER_ADDRESS   the deployed ledger
///   LEDGER_CANON     canon name, e.g. "rhdepth-v2"; must equal the validator's record
///   BACKFILL_LIST    ./backfill-list.jsonl
///   BACKFILL_CHECK   ./backfill-checked.json, written by tools/check_backfill_list.py --emit
///   COMMIT_BATCH     optional, default 64
///
/// Usage:
///   forge script script/Commit.s.sol --rpc-url $RPC_TESTNET            # dry run, sends nothing
///   forge script script/Commit.s.sol --rpc-url $RPC_TESTNET --broadcast
///
/// A dry run simulates every batch against the real chain state, so the whole plan and the
/// read-back of all batches are exercised without a key and without spending the watermark.
///
/// The six properties, and where each is enforced:
///
///   1. the list is validated first and no transaction is sent if it is not. Enforced by the
///      hash gate in `_loadValidated`: the validator's record carries the sha256 of the list's
///      bytes and the script recomputes it. A list never checked, or changed after checking,
///      cannot enter the broadcast path -- a precondition, not a step someone must remember.
///   2. `CANON()` must equal the canon in the record, whose value comes from canon.json, the
///      single source for name -> value. A v2 list cannot be written into a v3 ledger.
///   3. a watermark past the list's last block stops the run (`_resumeFrom`): somebody committed
///      forward, and this is the only place it can be caught before more damage is done.
///   4. after every batch the first, middle and last block are read back through `getRoundHash`
///      and compared with the list; a mismatch stops the remaining batches. The contract
///      guarantees strict increase, not that the hashes written are the right ones.
///   5. resume, never restart: committed blocks are skipped, since re-committing reverts on the
///      strict-increase rule. The prefix is not taken on trust -- a sample of it is read back
///      against the list first. This check is an addition to the six; it closes the hole resume
///      would otherwise open.
///   6. batch size comes from the rule and the batch count follows from the list, rather than a
///      fixed count being asserted.
contract Commit is Script {
    uint256 internal constant DEFAULT_BATCH = 64;

    struct Round {
        uint64 blockRef;
        bytes32 roundHash;
    }

    struct Cfg {
        uint256 chainId;
        address ledgerAddr;
        string canonName;
        string listPath;
        string checkPath;
        uint256 batchSize;
        uint8 canonValue;
    }

    error WrongChain(uint256 actual, uint256 expected);
    error CanonNameMismatch(string fromEnv, string fromRecord);
    error CanonMismatch(uint8 onchain, uint8 fromRecord, string name);
    error ListChanged(bytes32 recorded, bytes32 actual);
    error ListEmpty();
    error EntryCountMismatch(uint256 parsed, uint256 recorded);
    error ListNotAscending(uint256 index, uint64 previous, uint64 current);
    error ListNotUint64(uint256 index);
    error WatermarkPastListEnd(uint64 watermark, uint64 listEnd);
    error CommittedPrefixDisagrees(uint64 blockRef, bytes32 listed, bytes32 onchain, uint8 canon);
    error ReadbackFailed(uint64 blockRef, bytes32 listed, bytes32 onchain, uint8 canon);

    function run() external {
        Cfg memory cfg = _config();
        if (block.chainid != cfg.chainId) revert WrongChain(block.chainid, cfg.chainId);
        EvidenceLedger ledger = EvidenceLedger(cfg.ledgerAddr);

        Round[] memory all = _loadValidated(cfg, ledger);
        uint256 start = _resumeFrom(ledger, all, cfg.canonValue);
        _commitAll(ledger, all, start, cfg);
    }

    function _config() internal view returns (Cfg memory) {
        return Cfg({
            chainId: vm.envUint("LEDGER_CHAIN_ID"),
            ledgerAddr: vm.envAddress("LEDGER_ADDRESS"),
            canonName: vm.envString("LEDGER_CANON"),
            listPath: vm.envString("BACKFILL_LIST"),
            checkPath: vm.envString("BACKFILL_CHECK"),
            batchSize: vm.envOr("COMMIT_BATCH", DEFAULT_BATCH),
            canonValue: 0
        });
    }

    /// @dev Gates 1 and 2, then parse. A missing record makes readFile revert naming the path,
    ///      which is the refusal we want.
    function _loadValidated(Cfg memory cfg, EvidenceLedger ledger)
        internal
        view
        returns (Round[] memory)
    {
        string memory checkRaw = vm.readFile(cfg.checkPath);
        string memory raw = vm.readFile(cfg.listPath);

        bytes32 actual = sha256(bytes(raw));
        bytes32 recorded = vm.parseBytes32(vm.parseJsonString(checkRaw, ".listSha256"));
        if (recorded != actual) revert ListChanged(recorded, actual);

        string memory recordedCanon = vm.parseJsonString(checkRaw, ".canon");
        if (keccak256(bytes(recordedCanon)) != keccak256(bytes(cfg.canonName))) {
            revert CanonNameMismatch(cfg.canonName, recordedCanon);
        }
        uint8 recordedValue = uint8(vm.parseJsonUint(checkRaw, ".canonValue"));
        uint8 onchainCanon = ledger.CANON();
        if (onchainCanon != recordedValue) {
            revert CanonMismatch(onchainCanon, recordedValue, cfg.canonName);
        }
        // Assigned here rather than returned: the read-back gate compares against this value, and
        // an earlier version of this function left it at the struct's initialiser of 0. Zero is
        // the "absent" sentinel in this project, so the wrong value looked deliberate, and the dry
        // run failed with expected hash == actual hash while only the canon compared unequal.
        cfg.canonValue = recordedValue;

        Round[] memory all = _parse(raw);
        if (all.length == 0) revert ListEmpty();
        if (all.length != vm.parseJsonUint(checkRaw, ".entries")) {
            revert EntryCountMismatch(all.length, vm.parseJsonUint(checkRaw, ".entries"));
        }
        return all;
    }

    /// @dev Gates 3 and 5: refuse a watermark past the list, then verify the prefix we would
    ///      resume on rather than assuming it.
    function _resumeFrom(EvidenceLedger ledger, Round[] memory all, uint8 canon)
        internal
        view
        returns (uint256)
    {
        uint64 watermark = ledger.latestCommittedBlock();
        uint64 listEnd = all[all.length - 1].blockRef;
        if (watermark > listEnd) revert WatermarkPastListEnd(watermark, listEnd);

        uint256 start;
        while (start < all.length && all[start].blockRef <= watermark) start++;

        if (watermark != 0 && start != 0) {
            uint256[3] memory probe = [uint256(0), start / 2, start - 1];
            for (uint256 p = 0; p < probe.length; p++) {
                Round memory r = all[probe[p]];
                (bytes32 got, uint8 gotCanon) = ledger.getRoundHash(r.blockRef);
                if (got != r.roundHash || gotCanon != canon) {
                    revert CommittedPrefixDisagrees(r.blockRef, r.roundHash, got, gotCanon);
                }
            }
            console2.log("resuming: the committed prefix was sampled and agrees,", start, "blocks");
        }
        return start;
    }

    /// @dev Gate 6 (batch size is a rule, the count follows) and gate 4 (read back each batch).
    function _commitAll(EvidenceLedger ledger, Round[] memory all, uint256 start, Cfg memory cfg)
        internal
    {
        uint256 remaining = all.length - start;
        uint256 batches = (remaining + cfg.batchSize - 1) / cfg.batchSize;

        console2.log("");
        console2.log("=== commit plan ===");
        console2.log("ledger          ", cfg.ledgerAddr);
        console2.log("canon           ", cfg.canonName, cfg.canonValue);
        console2.log("list entries    ", all.length);
        console2.log("already covered ", start);
        console2.log("to commit       ", remaining);
        console2.log("batch size      ", cfg.batchSize);
        console2.log("batches         ", batches);
        console2.log("");

        if (remaining == 0) {
            console2.log("nothing to commit: the watermark already covers the list");
            return;
        }

        for (uint256 b = 0; b < batches; b++) {
            uint256 from = start + b * cfg.batchSize;
            uint256 to = from + cfg.batchSize;
            if (to > all.length) to = all.length;

            (uint64[] memory blocks, bytes32[] memory hashes) = _slice(all, from, to);
            vm.startBroadcast();
            ledger.commitBatch(blocks, hashes);
            vm.stopBroadcast();

            _readBack(ledger, blocks, hashes, cfg.canonValue, b);
        }

        console2.log("");
        console2.log("done. watermark now:", ledger.latestCommittedBlock());
    }

    function _slice(Round[] memory all, uint256 from, uint256 to)
        internal
        pure
        returns (uint64[] memory blocks, bytes32[] memory hashes)
    {
        uint256 n = to - from;
        blocks = new uint64[](n);
        hashes = new bytes32[](n);
        for (uint256 i = 0; i < n; i++) {
            blocks[i] = all[from + i].blockRef;
            hashes[i] = all[from + i].roundHash;
        }
    }

    /// @dev Gate 4. Without this the contract would happily store a wrong hash next to a right
    ///      block number, and a mistake in batch 1 would only be found in batch 14.
    function _readBack(
        EvidenceLedger ledger,
        uint64[] memory blocks,
        bytes32[] memory hashes,
        uint8 canon,
        uint256 batchIndex
    ) internal view {
        uint256 n = blocks.length;
        uint256[3] memory probe = [uint256(0), n / 2, n - 1];
        for (uint256 p = 0; p < probe.length; p++) {
            uint256 i = probe[p];
            (bytes32 got, uint8 gotCanon) = ledger.getRoundHash(blocks[i]);
            if (got != hashes[i] || gotCanon != canon) {
                revert ReadbackFailed(blocks[i], hashes[i], got, gotCanon);
            }
        }
        console2.log("batch committed and read back", batchIndex + 1, blocks[0], blocks[n - 1]);
    }

    /// @dev Reads the JSONL list. Lines are split on \n; a trailing newline yields an empty tail
    ///      that is skipped. Anything malformed reverts rather than being skipped: silently
    ///      dropping a line would shorten the list, and a short list still commits happily.
    function _parse(string memory raw) internal view returns (Round[] memory) {
        string[] memory lines = vm.split(raw, "\n");
        uint256 n;
        for (uint256 i = 0; i < lines.length; i++) {
            if (bytes(lines[i]).length != 0) n++;
        }
        Round[] memory out = new Round[](n);
        uint256 k;
        for (uint256 i = 0; i < lines.length; i++) {
            if (bytes(lines[i]).length == 0) continue;
            uint256 b = vm.parseJsonUint(lines[i], ".block");
            if (b == 0 || b > type(uint64).max) revert ListNotUint64(i);
            bytes32 h = vm.parseJsonBytes32(lines[i], ".roundKeccak");
            if (k > 0 && uint64(b) <= out[k - 1].blockRef) {
                revert ListNotAscending(i, out[k - 1].blockRef, uint64(b));
            }
            out[k] = Round(uint64(b), h);
            k++;
        }
        return out;
    }
}
