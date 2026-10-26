"""ASSURED-A1 Autonomous Biomedical Co-Scientist Agent (inspired by Stanford Biomni).

Provides a generalist biomedical reasoning & tool-execution loop (`A1` / `AssuredBiomniAgent`)
that decomposes natural-language genome engineering goals into multi-step plans, dynamically
executes tools from the ASSURED-E1 environment, runs external database queries (Ensembl,
ClinVar, UniProt, gnomAD, OpenTargets, PubMed), executes sandboxed code, and synthesizes
structured scientific reports.

Usage (Biomni-compatible SDK):
    from assistant.biomni_agent import A1

    agent = A1(llm="auto")
    result = agent.go(
        "Design a knockout for DIPK2A, check gnomAD constraint and UniProt domains, "
        "rank guides with Lindel, and compare SpCas9 vs Mad7."
    )
    print(result["reply"])
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from assistant.biomni_tools import (
    ASSURED_E1_TOOLS,
    execute_tool,
    list_environment_catalog,
)

load_dotenv()

KNOWN_GENES = {
    "APOE", "TP53", "DIPK2A", "NR2F2", "INS", "SCN5A", "LRP2", "SORCS1",
    "BRCA1", "BRCA2", "EGFR", "KRAS", "MYC", "PCSK9", "CFTR", "HBB", "ACTB", "GAPDH",
}

STOPWORDS = {
    "DESIGN", "CRISPR", "GUIDE", "GUIDES", "KNOCKOUT", "KNOCKIN", "MUTATION",
    "POINT", "CHECK", "QUERY", "SEARCH", "RANK", "LINDEL", "COMPARE", "ENZYME",
    "ENZYMES", "TRACE", "TRACES", "SANGER", "CLONE", "CLONES", "GENE", "TARGET",
    "HUMAN", "MOUSE", "WITH", "FROM", "USING", "AND", "FOR", "THE", "INTO",
    "UNIPROT", "GNOMAD", "CLINVAR", "PUBMED", "ENSEMBL", "NCBI", "BRUNELLO",
    "SPCAS9", "SACAS9", "SPRY", "MAD7", "CAS12A", "FLAG", "EGFP", "SPOT", "RNP",
    "PROTEIN", "DOMAIN", "DOMAINS", "CONSTRAINT", "REPORT", "ORDER", "ANALYZE",
    "ASSESS", "TERMINAL", "INTERNAL", "TAG", "DONOR", "PRIMER", "PRIMERS", "WHAT",
    "PLEASE", "RUN", "SHOW", "BEST", "HIGH", "LOW", "ALL", "EXON", "CODON",
}


def parse_scientific_intent(prompt: str, default_gene: str = "TP53") -> Dict[str, Any]:
    """Extract target gene, edit type, mutation, tag, enzyme, and requested tasks from natural language."""
    text = (prompt or "").strip()
    lower = text.lower()

    # 1. Detect Amino Acid Mutation (e.g., R176C, R175H, R154S, E342K)
    mut_match = re.search(r"\b([ACDEFGHIKLMNPQRSTVWY]\d{1,5}[ACDEFGHIKLMNPQRSTVWY])\b", text.upper())
    pos_match = re.search(r"\b([ACDEFGHIKLMNPQRSTVWY]\d{1,5})\b", text.upper())
    mutation = mut_match.group(1) if mut_match else ""

    # 2. Detect Gene Symbol
    gene = ""
    for known in KNOWN_GENES:
        if re.search(rf"\b{known}\b", text.upper()):
            gene = known
            break
    if not gene:
        # Look for "for <GENE>" or "in <GENE>" or uppercase gene-like tokens
        m_for = re.search(r"\b(?:for|target|gene|in|of)\s+([A-Za-z0-9]{2,10})\b", text)
        if m_for and m_for.group(1).upper() not in STOPWORDS:
            gene = m_for.group(1).upper()
    if not gene:
        tokens = re.findall(r"\b[A-Z][A-Z0-9]{2,8}\b", text)
        for tok in tokens:
            if tok not in STOPWORDS and tok != mutation:
                gene = tok
                break
    if not gene:
        if "r176c" in lower or "r154s" in lower:
            gene = "APOE"
        elif "r175h" in lower:
            gene = "TP53"
        else:
            gene = default_gene

    # 3. Detect Edit Type
    if any(k in lower for k in ["internal tag", "in-frame tag", "spot", "alphabtx"]):
        edit_type = "it"
        if not mutation and pos_match:
            mutation = pos_match.group(1)
        elif not mutation:
            mutation = "R154"
    elif any(k in lower for k in ["n-terminal", "n-term", "start codon", "nt tag"]):
        edit_type = "nt"
    elif any(k in lower for k in ["c-terminal", "c-term", "stop codon", "ct tag", "flag", "2xha", "egfp", "mcherry", "dtag", "sd40"]):
        edit_type = "ct" if not mut_match else "pm"
    elif mut_match or any(k in lower for k in ["point mutation", "snp", "missense", "hdr", "substitution"]):
        edit_type = "pm"
        if not mutation:
            mutation = "R176C" if gene == "APOE" else "R175H"
    else:
        edit_type = "ko"

    # 4. Detect Tag
    tag = "FLAG"
    for candidate_tag, pattern in [
        ("SPOT", r"\bspot\b"),
        ("alphaBtx", r"\balphabtx\b"),
        ("2xHA-only", r"\b2xha\b"),
        ("dTAG-V5", r"\bdtag\b"),
        ("SD40-V5", r"\bsd40\b"),
        ("T2A-EGFP", r"\begfp\b|\bgfp\b"),
        ("P2A-mCherry", r"\bmcherry\b"),
        ("FLAG", r"\bflag\b"),
    ]:
        if re.search(pattern, lower):
            tag = candidate_tag
            break

    # 5. Detect Enzyme
    enzyme = "spcas9"
    for enz_id, pat in [
        ("mad7", r"\bmad7\b"),
        ("lbcas12a", r"\blbcas12a\b"),
        ("ascas12a", r"\bascas12a\b|\benascas12a\b|\bcas12a\b"),
        ("sacas9", r"\bsacas9\b"),
        ("spry", r"\bspry\b"),
        ("spcas9ng", r"\bspcas9-ng\b|\bspcas9ng\b"),
    ]:
        if re.search(pat, lower):
            enzyme = enz_id
            break

    # 6. Detect Requested Capabilities
    wants_trace_qc = any(k in lower for k in ["trace", "ab1", "chromatogram", "sanger", "deconvol", "clone screen", "qc analysis"])
    wants_enzyme_compare = any(k in lower for k in ["compare", "all enzymes", "which enzyme", "vs ", "versus", "multi-enzyme", "nuclease comparison"])
    wants_lindel = edit_type == "ko" or any(k in lower for k in ["lindel", "lof", "loss of function", "nmd", "ko rank", "rank guide"])
    wants_clinvar = edit_type == "pm" or any(k in lower for k in ["clinvar", "pathogenic", "clinical", "variant"])
    wants_uniprot = any(k in lower for k in ["uniprot", "protein", "domain", "function", "structure", "alphafold", "comprehensive", "co-scientist"]) or True
    wants_gnomad = edit_type == "ko" or any(k in lower for k in ["gnomad", "constraint", "pli", "loeuf", "essential", "haploinsufficien"])
    wants_opentargets = any(k in lower for k in ["disease", "opentarget", "therapeutic", "phenotype"])
    wants_pubmed = any(k in lower for k in ["pubmed", "literature", "paper", "publication", "citation"])
    wants_brunello = edit_type == "ko" or any(k in lower for k in ["brunello", "library", "specificity", "fpbase"])
    wants_code_exec = any(k in lower for k in ["python", "script", "calculate", "simulate", "gc distribution", "code"])

    return {
        "gene": gene,
        "edit_type": edit_type,
        "mutation": mutation,
        "tag": tag,
        "enzyme": enzyme,
        "wants_trace_qc": wants_trace_qc,
        "wants_enzyme_compare": wants_enzyme_compare,
        "wants_lindel": wants_lindel,
        "wants_clinvar": wants_clinvar,
        "wants_uniprot": wants_uniprot,
        "wants_gnomad": wants_gnomad,
        "wants_opentargets": wants_opentargets,
        "wants_pubmed": wants_pubmed,
        "wants_brunello": wants_brunello,
        "wants_code_exec": wants_code_exec,
    }


class AssuredBiomniAgent:
    """Biomni-A1 style autonomous biomedical agent orchestrating ASSURED-E1 tools and databases."""

    def __init__(
        self,
        path: str | Path = "./data",
        llm: str = "auto",
        project_id: Optional[str] = None,
    ) -> None:
        self.path = Path(path)
        self.llm = llm
        self.project_id = project_id
        self.catalog = list_environment_catalog()
        self._native_biomni = None

        if os.getenv("BIOMNI_USE_NATIVE") == "1":
            try:
                from biomni.agent import A1 as NativeBiomniA1  # type: ignore

                self._native_biomni = NativeBiomniA1(path=str(self.path), llm=self.llm)
            except Exception:
                self._native_biomni = None

    def build_plan(self, prompt: str, intent: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Decompose a biomedical prompt into an ordered multi-step execution plan."""
        gene = intent["gene"]
        edit_type = intent["edit_type"]
        mutation = intent["mutation"]
        tag = intent["tag"]
        enzyme = intent["enzyme"]

        plan: List[Dict[str, Any]] = []

        # Step 1: Gene Reference & Transcript Resolution
        plan.append(
            {
                "step": len(plan) + 1,
                "title": f"Resolve {gene} Genomic Reference & Canonical Transcript",
                "tool": "fetch_gene_reference",
                "args": {"gene": gene, "species": "human"},
                "rationale": "Retrieve annotated GenBank record or Ensembl MANE Select transcript structure.",
            }
        )

        # Step 2: External Biomedical Evidence (UniProt + gnomAD / ClinVar / OpenTargets)
        if intent["wants_uniprot"]:
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Query UniProtKB Protein Architecture & Domains for {gene}",
                    "tool": "query_uniprot_protein",
                    "args": {"gene": gene},
                    "rationale": "Identify functional domains, protein length (aa), and AlphaFold structural model.",
                }
            )

        if intent["wants_gnomad"]:
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Query gnomAD Loss-of-Function Constraint (pLI / LOEUF) for {gene}",
                    "tool": "query_gnomad_constraint",
                    "args": {"gene": gene},
                    "rationale": "Assess haploinsufficiency vs biallelic KO requirement in human populations.",
                }
            )

        if intent["wants_clinvar"]:
            q_str = f"{gene} {mutation}".strip() if mutation else f"{gene} pathogenic"
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Search NCBI ClinVar for Pathogenic Variants ({q_str})",
                    "tool": "search_clinvar_variants",
                    "args": {"query": q_str, "max_results": 5},
                    "rationale": "Verify clinical significance andHGVS/protein coordinates in ClinVar.",
                }
            )

        if intent["wants_opentargets"]:
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Query Open Targets Platform for {gene} Disease Associations",
                    "tool": "query_opentargets",
                    "args": {"ensembl_id_or_gene": gene},
                    "rationale": "Retrieve top disease phenotypes associated with target perturbation.",
                }
            )

        if intent["wants_pubmed"]:
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Search PubMed Literature for {gene} CRISPR Genome Editing",
                    "tool": "search_pubmed_literature",
                    "args": {"query": f"{gene} CRISPR {mutation}".strip(), "max_results": 4},
                    "rationale": "Gather peer-reviewed literature precedents for target editing.",
                }
            )

        # Step 3: Core ASSURED CRISPR Design
        plan.append(
            {
                "step": len(plan) + 1,
                "title": f"Execute ASSURED CRISPR Design ({gene} · {edit_type.upper()} · {enzyme.upper()})",
                "tool": "design_crispr_edit",
                "args": {
                    "gene": gene,
                    "edit_type": edit_type,
                    "mutation": mutation,
                    "tag": tag,
                    "enzyme": enzyme,
                    "project_id": self.project_id,
                    "save_reports": True,
                },
                "rationale": "Select optimal guide RNAs, design silent-blocked HDR donors or KO pairs, and compute thermodynamic primers.",
            }
        )

        # Step 4: Lindel Repair-Outcome KO Ranking
        if intent["wants_lindel"] and edit_type == "ko":
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Rank Non-Terminal Coding Exon Guides with Lindel + NMD Rules ({gene})",
                    "tool": "rank_ko_guides_lindel",
                    "args": {"gene": gene, "top_n": 8},
                    "rationale": "Predict repair-outcome probabilities (frameshift, +1 insertion, LoF allele %, and biallelic KO clone %).",
                }
            )

        # Step 5: Multi-Enzyme Comparison
        if intent["wants_enzyme_compare"]:
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Compare All 7 CRISPR Nucleases Across {gene} Locus",
                    "tool": "compare_crispr_enzymes",
                    "args": {
                        "gene": gene,
                        "edit_type": edit_type,
                        "mutation": mutation,
                        "tag": tag,
                    },
                    "rationale": "Evaluate SpCas9, SpCas9-NG, SaCas9, SpRY, enAsCas12a, LbCas12a, and Mad7 side-by-side.",
                }
            )

        # Step 6: Curated Library / Primer Specificity Lookup
        if intent["wants_brunello"]:
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": f"Cross-Check Broad Brunello KO Library & FPbase Tags ({gene})",
                    "tool": "lookup_brunello_and_tags",
                    "args": {
                        "gene": gene,
                        "tag_query": tag if edit_type in ("ct", "nt", "it") else None,
                    },
                    "rationale": "Cross-validate selected spacers against empirical Brunello guides and tag properties.",
                }
            )

        # Step 7: Sanger Trace QC Deconvolution
        if intent["wants_trace_qc"]:
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": "Deconvolve Sanger Chromatograms (.ab1) & Rank Edited Clones",
                    "tool": "analyze_sanger_traces",
                    "args": {
                        "use_demo_apoe": True,
                        "workflow": "snp" if edit_type == "pm" else "knockout",
                        "project_id": self.project_id,
                    },
                    "rationale": "Quantify WT, Indel, and HDR allele fractions and classify clone zygosity.",
                }
            )

        # Step 8: Optional Biomni-A1 Code Execution for Clonal Screening Power Calculation
        if intent["wants_code_exec"] or edit_type == "ko":
            code_snippet = (
                "import math\n"
                "# Calculate minimum clones to screen for >=95% probability of >=2 homozygous KO clones\n"
                "lof_allele_prob = 0.85  # Lindel predicted LoF allele share\n"
                "editing_eff = 0.80      # Typical RNP electroporation efficiency\n"
                "p_biallelic = (lof_allele_prob * editing_eff) ** 2\n"
                "# Binomial tail: P(X >= 2) >= 0.95\n"
                "for n in range(4, 192):\n"
                "    p0 = (1 - p_biallelic) ** n\n"
                "    p1 = n * p_biallelic * ((1 - p_biallelic) ** (n - 1))\n"
                "    if 1 - (p0 + p1) >= 0.95:\n"
                "        print(f'p_biallelic={p_biallelic:.3f} -> Screen {n} clones for >=95% power to recover >=2 biallelic KO clones')\n"
                "        break\n"
            )
            plan.append(
                {
                    "step": len(plan) + 1,
                    "title": "Biomni-A1 Code Execution: Clonal Screening Power & Binomial Sizing",
                    "tool": "execute_bioinformatics_code",
                    "args": {
                        "code": code_snippet,
                        "language": "python",
                        "description": "Compute 96-well clonal screening sample size for >=95% recovery of >=2 biallelic clones",
                    },
                    "rationale": "Programmatically model clonal screening requirements from predicted allele probabilities.",
                }
            )

        return plan

    def _synthesize_markdown_report(
        self,
        prompt: str,
        intent: Dict[str, Any],
        steps: List[Dict[str, Any]],
    ) -> str:
        """Synthesize a comprehensive Biomni Co-Scientist report from executed tool outputs."""
        gene = intent["gene"]
        edit_type = intent["edit_type"].upper()
        lines: List[str] = [
            f"## 🧬 ASSURED Biomni Co-Scientist Report: **{gene}** ({edit_type})",
            f"**Objective:** _{prompt.strip()}_",
            "",
            f"Executed **{len(steps)}** autonomous workflow steps across the **ASSURED-E1** biomedical environment.",
            "",
        ]

        # Extract key step outputs
        by_tool = {s["tool"]: s["output"] for s in steps if isinstance(s.get("output"), dict)}

        # 1. Target & Biomedical Context
        ref_out = by_tool.get("fetch_gene_reference") or {}
        uni_out = by_tool.get("query_uniprot_protein") or {}
        gno_out = by_tool.get("query_gnomad_constraint") or {}
        clin_out = by_tool.get("search_clinvar_variants") or {}
        ot_out = by_tool.get("query_opentargets") or {}

        lines.append("### 1. Target Locus & Multi-Database Evidence")
        if ref_out.get("ok"):
            lines.append(
                f"- **Genomic Reference:** `{ref_out.get('source')}` "
                + (f"({ref_out.get('length_bp')} bp)" if ref_out.get("length_bp") else f"(Ensembl `{ref_out.get('ensembl_id')}`, Canonical `{ref_out.get('canonical_transcript')}`)")
            )
        if uni_out.get("found"):
            lines.append(
                f"- **UniProtKB (`{uni_out.get('uniprot_accession')}`):** {uni_out.get('protein_name')} "
                f"— **{uni_out.get('length_aa')} aa**. {uni_out.get('function', '')[:240]}"
            )
            domains = uni_out.get("domains_and_sites") or []
            if domains:
                dom_str = ", ".join(f"{d.get('description') or d.get('type')} ({d.get('start')}–{d.get('end')} aa)" for d in domains[:4])
                lines.append(f"  - **Annotated Domains/Sites:** {dom_str}")
        if gno_out.get("found"):
            lines.append(
                f"- **gnomAD v4 Constraint:** `pLI = {gno_out.get('pLI')}`, `LOEUF = {gno_out.get('LOEUF')}`, "
                f"`mis_z = {gno_out.get('mis_z')}` → _{gno_out.get('haploinsufficiency_interpretation')}_"
            )
        if clin_out.get("variants"):
            v0 = clin_out["variants"][0]
            lines.append(
                f"- **ClinVar Evidence:** `{v0.get('accession')}` — **{v0.get('title')}** "
                f"({v0.get('clinical_significance')})"
            )
        if ot_out.get("top_diseases"):
            dis_str = ", ".join(f"{d['disease']} (score {d['score']})" for d in ot_out["top_diseases"][:3])
            lines.append(f"- **Open Targets Disease Associations:** {dis_str}")
        lines.append("")

        # 2. Primary ASSURED CRISPR Design
        des_out = by_tool.get("design_crispr_edit") or {}
        if des_out.get("ok"):
            lines.append(
                f"### 2. ASSURED CRISPR Design (`{des_out.get('enzyme')}` · Release Status: `{str(des_out.get('release_status', 'ready')).upper()}`)"
            )
            ko_a = des_out.get("ko_assessment")
            if ko_a:
                lines.append(
                    f"- **Knockout Protein-Loss Assessment:** Tier **`{str(ko_a.get('tier', 'robust')).upper()}`** "
                    + (f"| Min Lindel LoF Alleles: **`{ko_a.get('min_lindel_lof_pct')}%`**" if ko_a.get("min_lindel_lof_pct") is not None else "")
                    + (f" | Inter-cut deletion: `{ko_a.get('between_cuts_bp')} bp`" if ko_a.get("between_cuts_bp") else "")
                )
                if ko_a.get("isoform_note"):
                    lines.append(f"  - _{ko_a['isoform_note']}_")

            guides = des_out.get("guides") or []
            if guides:
                lines.append("")
                lines.append("| Guide | Spacer (5'→3') | PAM | Strand | GC% | Offset | Lindel LoF % | Biallelic KO % |")
                lines.append("|---|---|---|---|---|---|---|---|")
                for g in guides:
                    lines.append(
                        f"| **{g.get('name')}** | `{g.get('spacer')}` | `{g.get('pam')}` | `{g.get('strand')}` | "
                        f"{g.get('gc_pct')}% | {g.get('cut_offset_bp', '—')} | "
                        f"{str(g.get('lindel_lof_allele_pct')) + '%' if g.get('lindel_lof_allele_pct') is not None else '—'} | "
                        f"{str(g.get('lindel_biallelic_ko_pct')) + '%' if g.get('lindel_biallelic_ko_pct') is not None else '—'} |"
                    )
                lines.append("")

            donors = des_out.get("donors") or []
            if donors:
                d0 = donors[0]
                lines.append(
                    f"- **Primary Repair Donor (`{d0.get('name')}`, {d0.get('length_nt')} nt):** "
                    f"Protection tier `{d0.get('protection_tier', 'strong')}`, Post-edit CFD `{d0.get('cfd_post_edit', 0)}`"
                )
                lines.append(f"  ```text\n  {d0.get('sequence')}\n  ```")

            primers = des_out.get("primers") or []
            if primers:
                p_str = " | ".join(f"**{p.get('name')}**: `{p.get('sequence')}` (Tm {p.get('tm')}°C)" for p in primers)
                lines.append(f"- **Verification Primers:** {p_str}")

            for w in des_out.get("warnings") or []:
                lines.append(f"- ⚠️ **Review Note:** {w}")
            lines.append("")

        # 3. Full-Locus Lindel Ranking
        lin_out = by_tool.get("rank_ko_guides_lindel") or {}
        if lin_out.get("ok") and lin_out.get("top_guides"):
            common_ex = lin_out.get("common_coding_exons") or []
            common_str = f" · Common Exon(s) Across All Isoforms: `{', '.join(f'Exon {e}' for e in common_ex)}`" if common_ex else ""
            lines.append(
                f"### 3. Locus-Wide Lindel KO Guide Ranking ({lin_out.get('total_candidates_ranked')} NGG Candidates{common_str})"
            )
            lines.append("| Rank | Spacer | Exon | Isoform Coverage | Strand | Panel Tier | Lindel Frameshift % | Predicted LoF Allele % | Biallelic KO Clone % |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for idx, r in enumerate(lin_out["top_guides"][:5]):
                iso_badge = f"{r.get('isoform_coverage_pct', 100)}% {'✅ Common' if r.get('is_common_exon') else ''}".strip()
                lines.append(
                    f"| {idx + 1} | `{r.get('spacer')}` | Exon {r.get('coding_exon')} | {iso_badge} | `{r.get('strand')}` | "
                    f"`{r.get('panel_tier')}` | {r.get('lindel_frameshift_pct')}% | "
                    f"**{r.get('predicted_LoF_allele_pct')}%** | **{r.get('predicted_biallelic_LoF_clone_pct')}%** |"
                )
            lines.append("")

        # 4. Multi-Enzyme Comparison
        cmp_out = by_tool.get("compare_crispr_enzymes") or {}
        if cmp_out.get("ok") and cmp_out.get("candidates"):
            lines.append(f"### 4. Multi-Nuclease Comparison (Recommended: `{cmp_out.get('recommended_enzyme')}`)")
            lines.append("| Nuclease | PAM | Eligible | Release Verdict | Guides | Top Spacer |")
            lines.append("|---|---|---|---|---|---|")
            for c in cmp_out["candidates"]:
                top_sp = (c.get("top_guides") or ["—"])[0]
                lines.append(
                    f"| **{c.get('enzyme_name') or c.get('enzyme_id')}** | `{c.get('pam', '—')}` | "
                    f"{'✅' if c.get('eligible') else '❌'} | `{str(c.get('release_status')).upper()}` | "
                    f"{c.get('guide_count', 0)} | `{top_sp}` |"
                )
            lines.append("")

        # 5. Sanger Trace QC Deconvolution
        qc_out = by_tool.get("analyze_sanger_traces") or {}
        if qc_out.get("ok"):
            pref = qc_out.get("preferred_clone") or {}
            lines.append("### 5. Sanger Chromatogram (`.ab1`) Deconvolution & Clone Ranking")
            lines.append(
                f"- **Top Recommended Clone:** `{pref.get('name')}` — Decision: **`{str(pref.get('decision', '')).upper()}`** "
                f"(HDR `{pref.get('intendedEditPct')}%`, Indel `{pref.get('indelPct')}%`, WT `{pref.get('wtPct')}%`)"
            )
            lines.append("")

        # 6. Code Execution Output
        code_out = by_tool.get("execute_bioinformatics_code") or {}
        if code_out.get("ok") and code_out.get("stdout"):
            lines.append("### 6. Biomni-A1 Programmatic Analysis Output")
            lines.append(f"```text\n{code_out['stdout']}\n```")
            lines.append("")

        # 7. PubMed Literature
        pub_out = by_tool.get("search_pubmed_literature") or {}
        if pub_out.get("papers"):
            lines.append("### 7. Relevant PubMed Literature")
            for p in pub_out["papers"]:
                lines.append(f"- [{p.get('title')}]({p.get('url')}) — _{p.get('first_author')} et al., {p.get('source')} ({p.get('pubdate')})_ [PMID: {p.get('pmid')}]")
            lines.append("")

        return "\n".join(lines)

    def _maybe_enhance_with_llm(self, prompt: str, deterministic_report: str) -> str:
        """Optionally refine/augment the structured report with an configured LLM (OpenAI / Gemini / Anthropic / Ollama)."""
        mode = (self.llm or os.getenv("ASSISTANT_MODE") or "auto").lower()
        api_key = os.getenv("OPENAI_API_KEY")
        if mode in ("auto", "openai") and api_key:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=api_key)
                resp = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    temperature=0.2,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are ASSURED-Biomni, an autonomous CRISPR genome engineering co-scientist. "
                                "Preserve all markdown tables, exact DNA sequences, Lindel percentages, and database "
                                "identifiers from the executed tool report below, and add a concise Executive Co-Scientist "
                                "Interpretation & Experimental Protocol Recommendation section at the top."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"User Request: {prompt}\n\nExecuted Tool Report:\n{deterministic_report}",
                        },
                    ],
                )
                llm_text = resp.choices[0].message.content
                if llm_text:
                    return llm_text
            except Exception:
                pass
        return deterministic_report

    def go(
        self,
        prompt: str,
        project_id: Optional[str] = None,
        default_gene: str = "TP53",
    ) -> Dict[str, Any]:
        """Execute an end-to-end Biomni-A1 autonomous workflow from a natural-language prompt."""
        t_start = time.time()
        if project_id:
            self.project_id = project_id

        intent = parse_scientific_intent(prompt, default_gene=default_gene)
        plan = self.build_plan(prompt, intent)

        executed_steps: List[Dict[str, Any]] = []
        raw_design_output: Optional[Dict[str, Any]] = None
        raw_qc_output: Optional[Dict[str, Any]] = None
        references: List[Dict[str, str]] = []

        for item in plan:
            tool_name = item["tool"]
            tool_args = item["args"]
            out = execute_tool(tool_name, tool_args)

            # Extract heavy raw payloads so step trace JSON stays lightweight
            if "_raw_design_output" in out:
                raw_design_output = out.pop("_raw_design_output")
            if "_raw_qc_output" in out:
                raw_qc_output = out.pop("_raw_qc_output")

            for label, fpath in (out.get("saved_reports") or {}).items():
                references.append({"step": f"{tool_name}:{label}", "path": fpath})

            # If design generated primers and lookup_brunello_and_tags is upcoming, inject primers for specificity check
            if tool_name == "design_crispr_edit" and out.get("primers"):
                primers = out["primers"]
                if len(primers) >= 2:
                    for future_item in plan:
                        if future_item["tool"] == "lookup_brunello_and_tags":
                            future_item["args"]["fwd_primer"] = primers[0].get("sequence")
                            future_item["args"]["rev_primer"] = primers[1].get("sequence")

            executed_steps.append(
                {
                    "step": item["step"],
                    "title": item["title"],
                    "tool": tool_name,
                    "args": tool_args,
                    "rationale": item["rationale"],
                    "status": "completed" if out.get("ok") else "warning",
                    "duration_ms": out.get("_duration_ms", 0),
                    "output": out,
                }
            )

        report_md = self._synthesize_markdown_report(prompt, intent, executed_steps)
        final_reply = self._maybe_enhance_with_llm(prompt, report_md)

        return {
            "ok": True,
            "prompt": prompt,
            "intent": intent,
            "plan": plan,
            "steps": executed_steps,
            "reply": final_reply,
            "references": references,
            "design_output": raw_design_output,
            "qc_output": raw_qc_output,
            "total_duration_ms": round((time.time() - t_start) * 1000, 1),
        }


# Biomni-compatible alias (`from assistant.biomni_agent import A1`)
A1 = AssuredBiomniAgent
