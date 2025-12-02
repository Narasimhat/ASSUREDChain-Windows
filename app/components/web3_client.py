import json
import os
from pathlib import Path
from typing import Any, Tuple

from dotenv import load_dotenv
from web3 import Web3
from web3.contract import Contract

load_dotenv()

RPC = os.getenv("WEB3_PROVIDER_URL", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", "11155111"))
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "")
ACCOUNT_PRIVATE_KEY = os.getenv("ACCOUNT_PRIVATE_KEY", "")

ABI_PATH = Path(__file__).resolve().parents[2] / "contracts" / "AssuredRegistry.abi.json"

_DEFAULT_ABI = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "contentHash", "type": "bytes32"},
            {"internalType": "string", "name": "step", "type": "string"},
            {"internalType": "string", "name": "metadataURI", "type": "string"},
        ],
        "name": "log",
        "outputs": [{"internalType": "uint256", "name": "id", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "name": "entries",
        "outputs": [
            {"internalType": "address", "name": "submitter", "type": "address"},
            {"internalType": "bytes32", "name": "contentHash", "type": "bytes32"},
            {"internalType": "string", "name": "step", "type": "string"},
            {"internalType": "string", "name": "metadataURI", "type": "string"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "nextId",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "id", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "submitter", "type": "address"},
            {"indexed": False, "internalType": "bytes32", "name": "contentHash", "type": "bytes32"},
            {"indexed": False, "internalType": "string", "name": "step", "type": "string"},
            {"indexed": False, "internalType": "string", "name": "metadataURI", "type": "string"},
            {"indexed": False, "internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "name": "Logged",
        "type": "event",
    },
]


def _load_abi() -> list[Any]:
    if ABI_PATH.exists():
        return json.loads(ABI_PATH.read_text(encoding="utf-8"))
    return _DEFAULT_ABI


def get_client() -> Tuple[Web3, Contract]:
    if not RPC:
        raise RuntimeError("WEB3_PROVIDER_URL missing in .env")
    if not CONTRACT_ADDRESS:
        raise RuntimeError("CONTRACT_ADDRESS missing in .env")
    w3 = Web3(Web3.HTTPProvider(RPC))
    if not w3.is_connected():
        raise RuntimeError("Unable to connect to Web3 provider")
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACT_ADDRESS),
        abi=_load_abi(),
    )
    return w3, contract


def _normalize_digest(hex_digest: str) -> str:
    digest = hex_digest.lower().removeprefix("0x")
    if len(digest) != 64:
        raise ValueError("Digest must be a 32-byte hex string (64 hex chars)")
    return "0x" + digest


def send_log_tx(hex_digest: str, step: str, metadata_uri: str = "") -> dict[str, Any]:
    if not ACCOUNT_PRIVATE_KEY:
        raise RuntimeError("ACCOUNT_PRIVATE_KEY missing in .env")

    w3, contract = get_client()
    account = w3.eth.account.from_key(ACCOUNT_PRIVATE_KEY)
    nonce = w3.eth.get_transaction_count(account.address)

    digest_bytes32 = _normalize_digest(hex_digest)
    call_kwargs = {"from": account.address}
    try:
        gas_estimate = contract.functions.log(digest_bytes32, step, metadata_uri).estimate_gas(call_kwargs)
    except Exception:
        gas_estimate = 200000
    gas_limit = int(gas_estimate * 1.2) + 5000

    tx = contract.functions.log(digest_bytes32, step, metadata_uri).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": CHAIN_ID,
            "gas": gas_limit,
            "maxFeePerGas": w3.to_wei(3, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        }
    )

    signed = w3.eth.account.sign_transaction(tx, private_key=ACCOUNT_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return {"tx_hash": tx_hash.hex(), "receipt": dict(receipt)}
