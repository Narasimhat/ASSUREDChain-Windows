# ASSUREDChain

Streamlit + Web3 workflow logger that mirrors the ASSURED protocol. Capture each stage as an immutable JSON snapshot, hash it (SHA-256), and optionally anchor the digest on Sepolia.

**Assured tools at the right steps**

- **Design Log** launches [Assured Design (FAIR)](https://assured-design-fair.vercel.app/) and imports `assured-qc-design/1` hand-off JSON into guides/donors/primers.
- **Assessment Log** launches [Assured QC (FAIR)](https://assured-qc-fair.vercel.app/) and imports results CSV/JSON into indel/HDR % plus cloning decision.
- Override URLs with `ASSURED_DESIGN_URL`, `ASSURED_QC_URL`, and `ASSURED_HUB_FAIR_URL` in `.env` (see `.env.template`).

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

## Deploy on Streamlit Community Cloud (free, recommended)

No Railway/Vercel account needed. Free hosting for this Streamlit app via GitHub.

**Trade-offs:** free Cloud storage is **ephemeral** (redeploys wipe `data/`). Fine for demos and teaching; keep real lab snapshots on a local/shared Windows install. The AI assistant runs **in-process** when no separate FastAPI server is available. Local Synthego ICE CLI is optional (`requirements-ice.txt`); prefer Assured QC FAIR imports.

1. Merge/push this branch, then open [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub → **New app**.
2. Repository: `Narasimhat/ASSUREDChain-Windows` · Branch: `main` (or this PR branch) · Main file path: `app/Home.py`.
3. Under **Advanced settings → Secrets**, paste from `.streamlit/secrets.toml.example` (at least `OPENAI_API_KEY` if you want the sidebar assistant).
4. Deploy. Open the public `*.streamlit.app` URL.

Local Windows installs that still need ICE batch:

```powershell
pip install -r requirements.txt
pip install -r requirements-ice.txt
pip install "synthego-ice<=1.2.0" --no-deps
```

### Optional: Railway / Docker (paid or self-hosted)

If you later want durable disks, use the included `Dockerfile` + `railway.toml` (Streamlit on `$PORT`, assistant on `127.0.0.1:8000`, volume at `/app/data`). Vercel is not a fit for this Python stack.

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


### Enzyme identity in design records

Design imports and Stage 2 approval retain the applied enzyme, its PAM motif/side and the supplied configuration version. All seven current Bench enzyme IDs are recognized, as are the legacy generic Cas12a and SpCas9 reagent labels. Actual guide PAM sequences are stored separately from the enzyme motif. Mixed, conflicting or unsupported declarations are rejected; observed concrete PAMs must be compatible before approval.

The approval enzyme is fixed to an explicitly labelled import. Older files without enzyme metadata use a visibly marked SpCas9 compatibility default; select the correct enzyme and confirm it before locking. Newly generated Chain CLI handoffs include explicit configuration metadata even for SpCas9. Current imports and in-app results are scoped to their Chain project. Newly locked snapshots and project compliance metadata retain the enzyme identity; existing snapshots, hashes and anchored records are not rewritten.

The in-app designer still requires a current ASSURED Bench checkout (or ASSURED_BENCH_ROOT) and Node.js. After updating this repository on the Windows host, restart Streamlit. The change does not provision Node/Bench into a separate cloud deployment.
