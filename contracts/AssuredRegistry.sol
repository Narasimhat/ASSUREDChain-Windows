// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract AssuredRegistry {
    event Logged(
        uint256 indexed id,
        address indexed submitter,
        bytes32 contentHash,
        string step,
        string metadataURI,
        uint256 timestamp
    );

    struct Entry {
        address submitter;
        bytes32 contentHash;
        string step;
        string metadataURI;
        uint256 timestamp;
    }

    uint256 public nextId;
    mapping(uint256 => Entry) public entries;

    function log(bytes32 contentHash, string calldata step, string calldata metadataURI)
        external
        returns (uint256 id)
    {
        id = nextId++;
        entries[id] = Entry(msg.sender, contentHash, step, metadataURI, block.timestamp);
        emit Logged(id, msg.sender, contentHash, step, metadataURI, block.timestamp);
    }
}
