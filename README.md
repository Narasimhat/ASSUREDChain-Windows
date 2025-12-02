# ASSUREDChain

Day 1 scaffold for a Streamlit + Web3 workflow logger that mirrors the ASSURED protocol. The current focus is capturing **Design** stage metadata, saving an immutable JSON snapshot, and computing a SHA-256 hash that will be anchored on-chain in a later iteration.

## Getting Started

```powershell
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -U pip
pip install -r requirements.txt
```

Copy `.env.template` to `.env` and populate fields if you want to experiment with Web3 connectivity ahead of Day 2.

### Enable the AI assistant

1. Copy `.env.template` to `.env` if you have not already.
2. Add your OpenAI key and optional model override:

   ```bash
   OPENAI_API_KEY="sk-..."
   OPENAI_MODEL="gpt-4o-mini"  # optional override
   ASSISTANT_MODE=openai
   ASSISTANT_API_URL=http://127.0.0.1:8000
   ```

3. Start the assistant backend (separate terminal):

   ```bash
   uvicorn assistant.backend.main:app --reload --port 8000
   ```

4. Launch Streamlit as usual; the assistant sidebar is available on workflow pages like **Design**.

If you prefer a local model, set `ASSISTANT_MODE=local` and point `ASSISTANT_LOCAL_ENDPOINT` / `ASSISTANT_LOCAL_MODEL` to your server.

Populate `.env` with your Sepolia endpoint, deployed contract address, chain ID, and the private key of a test account that holds Sepolia ETH. The repository includes `contracts/AssuredRegistry.abi.json`; replace it with the ABI exported from Remix if you redeploy.

Launch Streamlit:

```powershell
streamlit run app/Home.py --server.port 8503
```

The **Design Logger** page stores snapshots in `data/design_logs/` with filenames that include the JSON hash prefix. Tomorrow's update will push those hashes to the `AssuredRegistry` contract on Sepolia.

## Day 2: Anchor Design Hashes On-Chain

1. Deploy `contracts/AssuredRegistry.sol` to Sepolia (Remix + MetaMask).
2. Set `.env` values: `WEB3_PROVIDER_URL`, `CONTRACT_ADDRESS`, `CHAIN_ID`, `ACCOUNT_PRIVATE_KEY`.
3. Create at least one design snapshot; the page now exposes **Anchor hash on-chain (Sepolia)** which signs and broadcasts a `log` transaction via `web3.py`.
4. Inspect the returned transaction hash/receipt in the UI and verify on Sepolia Etherscan.

## Day 3: Delivery Logger

1. A new **Delivery** page captures Neon electroporation parameters (1200 V / 30 ms / 1 pulse), cell counts, buffer notes, and RNP mix composition aligned with the ASSURED protocol.
2. Snapshots are written to `data/delivery_logs/` and hashed with SHA-256.
3. Use **Anchor delivery hash on-chain (Sepolia)** to persist the hash to the same registry with `step="Delivery"`.
4. Confirm the transaction on Sepolia Etherscan and track the entry ID alongside your lab notes.

## On-chain Ledger (Day 3+)

- The **On-chain Ledger** page fetches `AssuredRegistry` entries directly from Sepolia using `web3.py`.
- Click **Refresh ledger** to pull the latest `Logged` events (Design, Delivery, etc.).
- Ensure `.env` is populated so the app can connect to your RPC endpoint; no signing is required for read calls.
- After anchoring, the Design and Delivery pages write a sibling `*.chainproof.json` file containing the tx hash, contract, chain ID, content hash, and decoded entry ID for lab notebooks.

## Day 4: Assessment Logger

1. The **Assessment** page ingests ICE/TIDE summaries (CSV/TXT/JSON) to auto-populate edit percentages while allowing manual overrides.
2. Snapshots (plus optional uploaded reports) are written to `data/assessment_logs/` alongside `*.chainproof.json` receipts after anchoring.
3. Anchor the assessment hash with `step="Assessment"` and confirm the transaction via Sepolia Etherscan or the On-chain Ledger page.

## Day 5: Cloning Logger (ASSURED)

1. Capture plate prep, media plan, Poisson seeding, selection criteria, and aggregate clone counts during isoCell → 96-well splitting.
2. Record screening/expansion plate metadata and store attachments in `data/cloning_assured_logs/` (images under `data/cloning_assured_logs/images/`) with matching `*.chainproof.json` receipts after anchoring `step="Cloning"`.
3. Attach isoCell/Cytena exports and clone proof images to preserve audit trails before screening.

## Day 6: Screening Summary

1. Log screening batches (32–48 clones): PCR band positives, sequencing submissions, and choose up to six positives to advance.
2. Snapshots and attachments live in `data/screening_clone_logs/` with `*.chainproof.json` receipts after anchoring `step="Screening"`.

## Day 7: Preliminary QC

1. Capture SNP edit evidence, SNP-array CNV/LOH interpretation, and validation assays (Western blot/other).
2. Snapshots and supporting files live in `data/prelim_qc_logs/` (images and docs subfolders) with `*.chainproof.json` receipts after anchoring `step="Preliminary QC"`.

## Next Steps

- Add seed-bank modules when you want to extend provenance beyond screening.
- Follow the established pattern: structured metadata → immutable JSON snapshot → SHA-256 digest → on-chain anchoring plus `*.chainproof.json` receipt.
## VS Code Dev Container

1. Install Docker Desktop and the VS Code Dev Containers extension.
2. From VS Code, choose **Dev Containers: Reopen in Container**; the Dockerfile pre-installs Python 3.11 plus project requirements.
3. Inside the container, run `streamlit run app/Home.py --server.port 8503` (port 8503 is forwarded automatically).
4. Copy or recreate your `.env` secrets in the container before anchoring hashes.

































## Day 6: Master Bank & QC Registry

1. Track the master bank/QC registry and anchor finalized entries.
2. Registry lives in data/registry/master_bank_registry.csv with anchored snapshots in data/registry/.
