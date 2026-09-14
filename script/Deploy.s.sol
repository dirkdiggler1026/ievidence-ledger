// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

import {Script, console2} from "forge-std/Script.sol";
import {EvidenceLedger} from "../src/EvidenceLedger.sol";

/// @title Deploy the evidence ledger
/// @notice Every value this script needs is supplied by the operator at run time. Nothing is
///         defaulted, and nothing is derived from `msg.sender` or from the environment
///         "if present". The reason is specific to this repository: `canon` was deliberately
///         left open in the implementation so that the spec decides it, and a deployment
///         script carrying a default would quietly make that decision instead. A value that
///         has to be typed by hand is the only evidence that the decision has not been
///         taken by something else.
///
/// Required environment:
///
///   LEDGER_CHAIN_ID   4663 (mainnet) or 46630 (testnet). Read, then asserted against
///                     `block.chainid`. A whitelist alone would not stop a mainnet
///                     deployment made by accident; stating the intended chain does.
///   LEDGER_CANON      Canon name, e.g. "rhdepth-v2". No fallback. Unknown names revert.
///   LEDGER_OWNER      The address that will own the ledger. Asserted to equal the
///                     broadcasting account, so a deployment cannot silently hand the write
///                     path to an address nobody controls. Passing an owner into the
///                     constructor would allow exactly that mistake, and a single typo there
///                     bricks the only write path, so the contract keeps `owner = msg.sender`
///                     and this script proves the sender is the intended one.
///
/// Usage:
///
///   forge script script/Deploy.s.sol --rpc-url $RPC_MAINNET                  # dry run
///   forge script script/Deploy.s.sol --rpc-url $RPC_TESTNET --broadcast     # sends
///
/// The dry run and the broadcast run execute the same code; the only difference is whether
/// `--broadcast` is passed. Then `tools/record_deployment.py` reads the broadcast artifact,
/// verifies the result against the chain, and appends the record.
contract Deploy is Script {
    uint64 internal constant CHAIN_MAINNET = 4663;
    uint64 internal constant CHAIN_TESTNET = 46630;

    error WrongChain(uint256 actual, uint256 expected);
    error UnknownChain(uint256 chainId);
    error UnknownCanon(string name);
    error OwnerIsNotBroadcaster(address owner, address broadcaster);

    /// @dev Canon name to on-chain value, read from `canon.json`.
    ///
    ///      spec §7 fixes the mapping; it is not a decision this script may make. Choosing
    ///      *which* canon to deploy is the operator's, and arrives through LEDGER_CANON.
    ///
    ///      The mapping lives in `canon.json` rather than here because it used to live in two
    ///      places at once -- a keccak comparison in this file and a literal `2` in
    ///      tools/record_deployment.py -- with nothing comparing them. On the day canon v3
    ///      arrives, adding "rhdepth-v3" here while the recorder still asserted 2 would have
    ///      rejected a correct v3 deployment, and editing one of the two would have been a
    ///      coin flip. One source, read by both.
    function _canonValue(string memory name) internal view returns (uint8) {
        string memory json = vm.readFile("canon.json");
        string memory path = string.concat(".canons[\"", name, "\"]");
        if (!vm.keyExistsJson(json, path)) revert UnknownCanon(name);
        uint256 value = vm.parseJsonUint(json, path);
        // Zero is refused here for the same reason the contract refuses it: it is the value
        // the absent-round signal occupies. The contract is the actual guarantee; this keeps
        // the script from producing a transaction that is certain to revert.
        if (value == 0 || value > type(uint8).max) revert UnknownCanon(name);
        return uint8(value);
    }

    function run() external returns (EvidenceLedger ledger) {
        // --- read, before anything is broadcast ----------------------------------------
        uint256 expectedChainId = vm.envUint("LEDGER_CHAIN_ID");
        if (expectedChainId != CHAIN_MAINNET && expectedChainId != CHAIN_TESTNET) {
            revert UnknownChain(expectedChainId);
        }
        // An intent that was stated has to match the chain actually being talked to. This is
        // the cheap half of the guard: it stops a testnet deployment from landing on mainnet
        // because the wrong --rpc-url was passed.
        if (block.chainid != expectedChainId) {
            revert WrongChain(block.chainid, expectedChainId);
        }

        string memory canonName = vm.envString("LEDGER_CANON");
        uint8 canon = _canonValue(canonName);

        address expectedOwner = vm.envAddress("LEDGER_OWNER");

        // --- broadcast ------------------------------------------------------------------
        vm.startBroadcast();

        // Inside the broadcast context `msg.sender` is the broadcasting account. Assert before
        // deploying: a mismatch must cost nothing.
        if (msg.sender != expectedOwner) {
            revert OwnerIsNotBroadcaster(expectedOwner, msg.sender);
        }

        ledger = new EvidenceLedger(canon);

        vm.stopBroadcast();

        // --- what the operator records by hand ------------------------------------------
        console2.log("");
        console2.log("=== deployment ===");
        console2.log("chainId      ", block.chainid);
        console2.log("address      ", address(ledger));
        console2.log("owner        ", ledger.owner());
        console2.log("canon (name) ", canonName);
        console2.log("canon (value)", ledger.CANON());
        console2.log("watermark    ", ledger.latestCommittedBlock());
        console2.log("");
        console2.log("next: python tools/record_deployment.py --chain-id", block.chainid);
        console2.log("");

        _printCompilerSettings();
    }

    /// @dev Blockscout verification has to be given the compiler settings that were actually
    ///      used. Printing them from `foundry.toml` is weaker than printing what solc did, but
    ///      it is the same file the build read, and it makes a drift between the two visible
    ///      at deploy time rather than at verification time.
    function _printCompilerSettings() internal view {
        string memory toml = vm.readFile("foundry.toml");
        string memory solcVersion = vm.parseTomlString(toml, ".profile.default.solc_version");
        string memory evmVersion = vm.parseTomlString(toml, ".profile.default.evm_version");
        bool optimizer = vm.parseTomlBool(toml, ".profile.default.optimizer");
        uint256 runs = vm.parseTomlUint(toml, ".profile.default.optimizer_runs");

        console2.log("=== compiler (from foundry.toml) ===");
        console2.log("solc_version   ", solcVersion);
        console2.log("evm_version    ", evmVersion);
        console2.log("optimizer      ", optimizer);
        console2.log("optimizer_runs ", runs);
        console2.log("");
    }
}
