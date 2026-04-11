// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title AIAttestationRegistry
 * @author ChainNomads (AION Finance)
 * @notice Immutable on-chain log of AI inference attestations.
 *
 * @dev Every time the AION AI engine calls Claude for a yield prediction or
 *      allocation recommendation, it pays KITE via x402 and receives a tx hash
 *      as payment proof. That proof — along with a keccak256 digest of the
 *      inference output — is written here by an authorized reporter, creating
 *      a tamper-proof audit trail of every AI decision the protocol has made.
 *
 *      Attestation record:
 *        agentId     — which AI agent submitted the inference
 *        actionType  — "predict" | "analyze" | "get_yield_prediction" | etc.
 *        outputHash  — keccak256(abi.encode(inferenceOutput))
 *        kitePayTx   — Kite chain tx hash of the x402 micropayment
 *        timestamp   — block timestamp
 *
 *      External verifiers can independently confirm:
 *        1. outputHash matches the reported prediction/recommendation
 *        2. kitePayTx exists on Kite testnet (chain 2368) and was successful
 */
contract AIAttestationRegistry is Ownable {

    // ============================================================
    //                       STORAGE
    // ============================================================

    /// @dev Authorized addresses that can write attestations (AI engine reporter)
    mapping(address => bool) public authorizedReporters;

    /// @dev All attestations, append-only
    Attestation[] public attestations;

    /// @dev Attestations per agent for quick lookup
    mapping(address => uint256[]) public attestationsByAgent;

    /// @dev Attestations per actionType
    mapping(bytes32 => uint256[]) public attestationsByAction;

    /// @dev Total attestation count (convenience)
    uint256 public attestationCount;

    // ============================================================
    //                       DATA TYPES
    // ============================================================

    struct Attestation {
        address agentId;      // AI agent that produced the inference
        string  actionType;   // Tool/endpoint name ("predict", "get_allocation_recommendation", …)
        bytes32 outputHash;   // keccak256 of the inference output payload
        string  kitePayTx;    // Kite chain tx hash (x402 payment proof)
        uint256 timestamp;    // block.timestamp at write time
        uint256 blockNumber;  // block.number at write time
    }

    // ============================================================
    //                        EVENTS
    // ============================================================

    event AttestationRecorded(
        uint256 indexed attestationId,
        address indexed agentId,
        string  actionType,
        bytes32 outputHash,
        string  kitePayTx,
        uint256 timestamp
    );

    event ReporterUpdated(address indexed reporter, bool authorized);

    // ============================================================
    //                      CONSTRUCTOR
    // ============================================================

    constructor(address initialOwner) Ownable(initialOwner) {
        // Owner is an authorized reporter by default
        authorizedReporters[initialOwner] = true;
    }

    // ============================================================
    //                     MODIFIERS
    // ============================================================

    modifier onlyReporter() {
        require(authorizedReporters[msg.sender], "Not an authorized reporter");
        _;
    }

    // ============================================================
    //                  WRITE ATTESTATION
    // ============================================================

    /**
     * @notice Record a new AI inference attestation on-chain.
     *
     * @param agentId     Address of the AI agent (EOA or contract) that ran inference
     * @param actionType  Human-readable label: "predict", "analyze", "get_yield_prediction", …
     * @param outputHash  keccak256(abi.encode(inferenceOutputJSON)) — binds output to this record
     * @param kitePayTx   Kite testnet tx hash of the x402 KITE micropayment
     * @return id         Index of the new attestation in the attestations array
     */
    function recordAttestation(
        address agentId,
        string calldata actionType,
        bytes32 outputHash,
        string calldata kitePayTx
    ) external onlyReporter returns (uint256 id) {
        id = attestations.length;

        attestations.push(Attestation({
            agentId:     agentId,
            actionType:  actionType,
            outputHash:  outputHash,
            kitePayTx:   kitePayTx,
            timestamp:   block.timestamp,
            blockNumber: block.number
        }));

        attestationsByAgent[agentId].push(id);
        attestationsByAction[keccak256(bytes(actionType))].push(id);
        attestationCount = id + 1;

        emit AttestationRecorded(id, agentId, actionType, outputHash, kitePayTx, block.timestamp);
    }

    /**
     * @notice Convenience helper: hash an arbitrary bytes payload the same way
     *         the AI engine does before calling recordAttestation.
     */
    function hashOutput(bytes calldata output) external pure returns (bytes32) {
        return keccak256(output);
    }

    // ============================================================
    //                    VIEW FUNCTIONS
    // ============================================================

    function getAttestation(uint256 id) external view returns (Attestation memory) {
        require(id < attestations.length, "Out of range");
        return attestations[id];
    }

    function getAttestationsByAgent(address agent)
        external view returns (uint256[] memory)
    {
        return attestationsByAgent[agent];
    }

    function getAttestationsByAction(string calldata actionType)
        external view returns (uint256[] memory)
    {
        return attestationsByAction[keccak256(bytes(actionType))];
    }

    /// @notice Return the N most recent attestations (newest first).
    function getRecent(uint256 n)
        external view returns (Attestation[] memory result)
    {
        uint256 total = attestations.length;
        uint256 count = n < total ? n : total;
        result = new Attestation[](count);
        for (uint256 i = 0; i < count; i++) {
            result[i] = attestations[total - 1 - i];
        }
    }

    // ============================================================
    //                    ADMIN FUNCTIONS
    // ============================================================

    function setAuthorizedReporter(address reporter, bool authorized) external onlyOwner {
        authorizedReporters[reporter] = authorized;
        emit ReporterUpdated(reporter, authorized);
    }
}
