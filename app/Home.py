"""ASSUREDChain - FDA-Grade Precision Cell Line Manufacturing & FAIR Blockchain Registry."""

import sys
from pathlib import Path

import streamlit as st

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from app.components.layout import init_page


def main() -> None:
    init_page("ASSUREDChain")
    st.title("ASSUREDChain")
    st.markdown(
        """
        **ASSUREDChain** is an inspection-ready genome editing execution system designed according to FDA CBER/CDER human cell therapy guidance and 21 CFR Part 11 electronic records integrity.
        From initial **Project Request** to releasing **Two Independent Validated Clonal Cell Lines**, every critical quality attribute (CQA) is verified in-app and immutably anchored to the **Ethereum Sepolia FAIR Blockchain**.
        """
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Product Deliverable", "2 Validated Clones", "Primary + Backup Line")
        st.caption("Bi-allelic sequence confirmation, karyotype, STR match, sterility & viability.")
    with col2:
        st.metric("Lifecycle Stages", "6 FDA Stages", "Intake -> Master Bank")
        st.caption("Clear line of custody from user specification to master cell bank inventory.")
    with col3:
        st.metric("FAIR Ledger", "SHA-256 + Sepolia", "21 CFR Part 11")
        st.caption("Cryptographic audit receipts and verifiable Certificate of Analysis (CoA).")

    st.divider()
    st.subheader("🎯 6-Stage FDA Cell Line Production Pipeline")

    st.markdown(
        """
        | Stage | Mission | Key In-Process & Release Deliverables |
        | :--- | :--- | :--- |
        | **1. Project Request & TPP** | Intake & Target Product Profile | Target gene, mutation, parent cell line authentication, and 2-clone acceptance criteria. |
        | **2. CRISPR Design** | In-Silico Design & Reagent Specs | SpCas9 high-fidelity guides (CFD scores), HDR donor with silent PAM blockers, and PCR primer pairs. |
        | **3. Transfection & Bulk QC** | Delivery & Bulk In-Process Control | Electroporation parameters, post-pulse viability, and 48h pool trace deconvolution. |
        | **4. Single-Cell Cloning** | Monoclonality Assurance (FDA CQA) | Single-cell deposition proof (optical verification), 96-well expansion, and clone candidate selection. |
        | **5. Dual-Clone QC** | Comprehensive Release Testing | Side-by-side release matrix for Clone A and Clone B (On-Target, Off-Target, Karyotype, Sterility, STR, Pluripotency). |
        | **6. Master Bank & CoA** | Product Release & FAIR Anchoring | Master bank cryopreservation inventory, official regulatory Certificate of Analysis (Word/PDF), and Sepolia blockchain sealing. |
        """
    )

    st.divider()
    st.subheader("🚀 Get Started")
    col_start1, col_start2 = st.columns([1, 1])
    with col_start1:
        st.page_link("pages/1_Project_Request.py", label="Start Stage 1: Project Request & TPP", icon="📋")
    with col_start2:
        st.page_link("pages/0_Project_Dashboard.py", label="View Executive Batch Release Dashboard", icon="📊")

    st.divider()
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        protocol_path = PROJECT_ROOT / "assets" / "ASSURED_CRISPR_PROTOCOL.pdf"
        if protocol_path.exists():
            with open(protocol_path, "rb") as handle:
                st.download_button(
                    "📥 Download Published ASSURED Protocol (PDF)",
                    handle,
                    file_name="ASSURED_CRISPR_PROTOCOL.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
    with col_p2:
        st.link_button(
            "🌐 View Official STAR Protocols Publication",
            "https://star-protocols.cell.com/protocols/2872",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()