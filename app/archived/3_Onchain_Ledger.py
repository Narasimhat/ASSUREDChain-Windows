import sys
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from app.components.layout import init_page

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.components.web3_client import get_client  # noqa: E402


def fetch_entries() -> list[dict]:
    w3, contract = get_client()
    total = contract.functions.nextId().call()
    entries = []
    for entry_id in range(total):
        entry = contract.functions.entries(entry_id).call()
        entries.append(
            {
                "id": entry_id,
                "submitter": entry[0],
                "content_hash": entry[1].hex(),
                "step": entry[2],
                "metadata_uri": entry[3],
                "timestamp": entry[4],
            }
        )
    return entries

init_page("Step 3 - On-chain Ledger")
st.title("Step 3 - On-chain Ledger")
st.caption(
    "View hashes anchored to the AssuredRegistry contract. Requires `.env` to be configured with RPC, contract address, and private key (read-only usage does not sign transactions)."
)

if st.button("Refresh ledger"):
    st.session_state.pop("ledger_entries", None)

if "ledger_entries" not in st.session_state:
    try:
        with st.spinner("Fetching entries from Sepolia..."):
            st.session_state["ledger_entries"] = fetch_entries()
    except Exception as exc:
        st.error(f"Failed to load ledger: {exc}")

entries = st.session_state.get("ledger_entries", [])
if entries:
    st.success(f"Loaded {len(entries)} entries.")
    for entry in entries:
        st.subheader(f"Entry #{entry['id']} — {entry['step']}")
        st.write(f"Submitter: `{entry['submitter']}`")
        st.code(f"Content hash: {entry['content_hash']}", language="text")
        timestamp = datetime.fromtimestamp(entry["timestamp"], UTC).isoformat()
        st.caption(f"Timestamp (UTC): {timestamp}")
        st.write(f"Metadata URI: {entry['metadata_uri']}")
        tx_hint = (
            "Lookup by transaction hash in the Events tab on Etherscan."
            if not entry["metadata_uri"]
            else ""
        )
        if tx_hint:
            st.caption(tx_hint)
else:
    st.info("No entries found yet. Submit Design or Delivery logs to populate the ledger.")
