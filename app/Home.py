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
        **ASSUREDChain** keeps CRISPR projects inspection-ready by pairing the ASSURED protocol
        with immutable chainproof receipts. Capture every experiment, register the evidence,
        and anchor the hash on-chain without leaving Streamlit.
        """
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("ASSURED steps captured", "7", "Design -> QC")
        st.write("Structured forms for Design, Delivery, Assessment, Cloning, Screening, Seed and Master Bank.")
    with col2:
        st.metric("Registry records", "Immutable")
        st.write("Snapshots land in versioned registries with automated readiness scoring.")
    with col3:
        st.metric("Chainproof receipts", "SHA-256 + Sepolia")
        st.write("Push hashes to the AssuredRegistry smart contract with built-in audit logging.")

    st.divider()
    st.subheader("Why this matters")
    st.markdown(
        """
        - **Traceability:** Every critical data point lives in a structured registry and carries a verifiable hash.
        - **Audit readiness:** Chainproof JSON receipts bundle contract, chain ID, content hash, and tx metadata.
        - **Operational focus:** Pre-baked templates and checklists reduce drift across labs and edit programs.
        """
    )

    st.subheader("Workflow overview")
    workflow_cols = st.columns(3)
    workflow_steps = [
        ("Design", "Document guides, donors, primers, and replicates."),
        ("Registry", "Review readiness scores and sign-off with Master/Seed bank controls."),
        ("Chainproof", "Ship hashes on-chain and sync receipts back to your quality notebooks."),
    ]
    for col, (title, description) in zip(workflow_cols, workflow_steps):
        with col:
            col.markdown(f"### {title}")
            col.write(description)

    st.divider()
    st.subheader("Case studies")
    case_tabs = st.tabs(["Seed Bank Release", "Regulatory Audit"])
    with case_tabs[0]:
        st.markdown(
            """
            **BIHi005-A Seed Bank Release**  
            - Design snapshot validated with HDR donor template and STR report.  
            - Delivery and Screening snapshots linked to Neon exports and gel imagery.  
            - Master bank registry marked ready -> chainproof anchored in under 10 minutes.  
            """
        )
    with case_tabs[1]:
        st.markdown(
            """
            **Berlin S3 Audit Prep**  
            - LAGESO compliance manager exported a bundle with Form Z, GenTAufzV, and SOP evidence.  
            - Chainproof receipts shared with inspectors, eliminating manual spreadsheets.  
            """
        )

    st.divider()
    st.subheader("Get started")
    st.write("Use the interactive protocol wizard to preload templates and jump directly into each stage.")
    col_start, col_protocol = st.columns([2, 1])
    with col_start:
        st.page_link("pages/15_Assured_Protocol_Wizard.py", label="Launch protocol wizard", icon="🚀")
        st.page_link("pages/0_Project_Dashboard.py", label="View project dashboard", icon="📊")
    with col_protocol:
        protocol_path = "assets/ASSURED_CRISPR_PROTOCOL.pdf"
        try:
            with open(protocol_path, "rb") as handle:
                st.download_button(
                    "Download ASSURED protocol",
                    handle,
                    file_name="ASSURED_CRISPR_PROTOCOL.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
        except FileNotFoundError:
            st.caption("Protocol PDF not found in assets/.")
        st.link_button(
            "Open official protocol",
            "https://star-protocols.cell.com/protocols/2872",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
