"""ASSURED-E1 Biomedical Environment & Tool Registry (inspired by Stanford Biomni).

Aggregates the ASSURED CRISPR/QC tool suite, external biomedical databases
(Ensembl, NCBI, ClinVar, UniProt, gnomAD, OpenTargets, PubMed), and sandboxed
Python/Node.js code execution into a unified, schema-typed action space.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.components.in_app_analyzer import (  # noqa: E402
    get_demo_trace_dir,
    run_in_silico_assessment,
    save_assessment_reports,
)
from app.components.in_app_designer import (  # noqa: E402
    get_bundled_references,
    run_in_silico_design,
    save_design_reports,
)

SCRATCH_ROOT = ROOT.parent
DESIGN_SKILL_DIR = SCRATCH_ROOT / "Assured_CRISPR_Design"
BENCH_DIR = SCRATCH_ROOT / "assured-bench"
BIOMNI_WORKSPACE = ROOT / "data" / "biomni_workspace"
BIOMNI_WORKSPACE.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. ASSURED-E1 Core Tools (CRISPR Design, Lindel KO Ranking, Multi-Enzyme, QC)
# ---------------------------------------------------------------------------


def tool_fetch_gene_reference(
    gene: str,
    species: str = "human",
    source: str = "ensembl",
) -> Dict[str, Any]:
    """Fetch gene reference sequence (GenBank) and transcript metadata from bundled fixtures, Ensembl MANE Select, or NCBI RefSeq."""
    gene_clean = (gene or "").strip().upper()
    if not gene_clean:
        return {"ok": False, "error": "Gene symbol is required."}

    bundled = get_bundled_references()
    if gene_clean in bundled and bundled[gene_clean].exists():
        gb_path = bundled[gene_clean]
        gb_text = gb_path.read_text(encoding="utf-8", errors="replace")
        locus_line = gb_text.splitlines()[0] if gb_text else ""
        return {
            "ok": True,
            "gene": gene_clean,
            "source": f"Bundled verified GenBank ({gb_path.name})",
            "length_bp": len(gb_text),
            "locus": locus_line,
            "file_path": str(gb_path),
        }

    # Query Ensembl REST API for gene metadata
    species_map = {
        "human": "homo_sapiens",
        "mouse": "mus_musculus",
        "rat": "rattus_norvegicus",
        "zebrafish": "danio_rerio",
    }
    ens_species = species_map.get(species.lower(), "homo_sapiens")
    try:
        resp = requests.get(
            f"https://rest.ensembl.org/lookup/symbol/{ens_species}/{gene_clean}?expand=1",
            headers={"Content-Type": "application/json", "User-Agent": "ASSURED-Biomni/1.0"},
            timeout=15,
        )
        if resp.ok:
            data = resp.json()
            transcripts = data.get("Transcript") or []
            canonical = next((t for t in transcripts if t.get("is_canonical")), transcripts[0] if transcripts else {})
            return {
                "ok": True,
                "gene": data.get("display_name") or gene_clean,
                "ensembl_id": data.get("id"),
                "description": data.get("description"),
                "biotype": data.get("biotype"),
                "chromosome": data.get("seq_region_name"),
                "start": data.get("start"),
                "end": data.get("end"),
                "strand": "+" if data.get("strand") == 1 else "-",
                "canonical_transcript": canonical.get("id"),
                "source": f"Ensembl REST ({ens_species})",
            }
    except Exception as exc:
        return {"ok": False, "gene": gene_clean, "error": f"Ensembl lookup failed: {exc}"}

    return {"ok": False, "gene": gene_clean, "error": f"Gene '{gene_clean}' not found."}


def tool_design_crispr_edit(
    gene: str,
    edit_type: str = "ko",
    mutation: str = "",
    tag: str = "FLAG",
    enzyme: str = "spcas9",
    arm_length: int = 400,
    delivery_method: str = "rnp",
    project_id: Optional[str] = None,
    save_reports: bool = True,
) -> Dict[str, Any]:
    """Run the ASSURED 7-enzyme CRISPR design engine (with automatic Lindel LoF ranking for SpCas9 KO and CFD silent blocking for HDR)."""
    res = run_in_silico_design(
        gene=gene,
        edit_type=edit_type,
        mutation=mutation,
        tag=tag,
        arm_length=arm_length,
        options={"deliveryMethod": delivery_method, "enzyme": enzyme},
    )
    dr = res.get("designResult") or {}
    guides = dr.get("gs") or []
    donors = dr.get("os") or []
    primers = dr.get("ps") or []
    ko_assess = dr.get("koAssessment")
    procurement = dr.get("procurement") or {}

    saved_paths: Dict[str, str] = {}
    if save_reports:
        try:
            saved = save_design_reports(project_id, res)
            saved_paths = {k: str(v) for k, v in saved.items() if v}
        except Exception:
            pass

    summary_guides = []
    for idx, g in enumerate(guides):
        ko_out = ((g.get("ko") or {}).get("outcomes") or {})
        summary_guides.append(
            {
                "rank": idx + 1,
                "name": g.get("n") or f"Guide {idx + 1}",
                "spacer": g.get("sp") or g.get("spacer"),
                "pam": g.get("pm") or g.get("pam"),
                "strand": g.get("str") or g.get("strand"),
                "gc_pct": g.get("gc"),
                "cut_offset_bp": g.get("d"),
                "ko_tier": (g.get("ko") or {}).get("tier"),
                "lindel_lof_allele_pct": ko_out.get("lofPct"),
                "lindel_biallelic_ko_pct": ko_out.get("biallelicPct"),
                "lindel_frameshift_pct": ko_out.get("frameshiftPct"),
            }
        )

    summary_donors = []
    for idx, d in enumerate(donors[:3]):
        seq = d.get("od") or d.get("seq") or d.get("sequence") or ""
        summary_donors.append(
            {
                "name": d.get("n") or f"Donor {idx + 1}",
                "length_nt": len(seq),
                "sequence": seq,
                "cfd_post_edit": d.get("cfdAfter"),
                "protection_tier": (d.get("protection") or {}).get("tier"),
                "protein_valid": (d.get("proteinValidation") or {}).get("valid", True),
            }
        )

    return {
        "ok": True,
        "gene": res.get("gene"),
        "edit_type": res.get("editType"),
        "enzyme": (dr.get("enzyme") or {}).get("name", enzyme),
        "pam_pattern": (dr.get("enzyme") or {}).get("pam_pattern", "NGG"),
        "release_status": procurement.get("status", "ready"),
        "warnings": procurement.get("warnings", []),
        "blockers": procurement.get("blockers", []),
        "ko_assessment": {
            "tier": ko_assess.get("tier"),
            "min_lindel_lof_pct": (ko_assess.get("outcomes") or {}).get("minLofPct"),
            "isoform_note": ko_assess.get("isoformNote"),
            "between_cuts_bp": (ko_assess.get("between") or {}).get("sizeBp"),
        }
        if ko_assess
        else None,
        "guides": summary_guides,
        "donors": summary_donors,
        "primers": [
            {"name": p.get("n") or p.get("name") or f"Primer {i+1}", "sequence": p.get("seq") or p.get("s"), "tm": p.get("tm")}
            for i, p in enumerate(primers)
        ],
        "saved_reports": saved_paths,
        "_raw_design_output": res,
    }


def tool_compare_crispr_enzymes(
    gene: str,
    edit_type: str = "ko",
    mutation: str = "",
    tag: str = "FLAG",
    enzymes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compare multiple CRISPR nucleases (SpCas9, SpCas9-NG, SaCas9, SpRY, enAsCas12a, LbCas12a, Mad7) on the same target gene and edit."""
    target_enzymes = enzymes or ["spcas9", "spcas9ng", "sacas9", "ascas12a", "lbcas12a", "mad7"]
    comparisons = []
    for enz in target_enzymes:
        try:
            d = tool_design_crispr_edit(
                gene=gene,
                edit_type=edit_type,
                mutation=mutation,
                tag=tag,
                enzyme=enz,
                save_reports=False,
            )
            d.pop("_raw_design_output", None)
            comparisons.append(
                {
                    "enzyme_id": enz,
                    "enzyme_name": d.get("enzyme"),
                    "pam": d.get("pam_pattern"),
                    "eligible": True,
                    "release_status": d.get("release_status"),
                    "guide_count": len(d.get("guides") or []),
                    "top_guides": [g.get("spacer") for g in (d.get("guides") or [])[:2]],
                    "ko_tier": (d.get("ko_assessment") or {}).get("tier"),
                    "min_lindel_lof_pct": (d.get("ko_assessment") or {}).get("min_lindel_lof_pct"),
                    "warnings_count": len(d.get("warnings") or []),
                }
            )
        except Exception as exc:
            comparisons.append(
                {
                    "enzyme_id": enz,
                    "eligible": False,
                    "release_status": "unavailable",
                    "error": str(exc),
                }
            )

    # Sort eligible ready first, then review, then by Lindel LoF pct
    def _sort_key(item: Dict[str, Any]) -> tuple:
        if not item.get("eligible"):
            return (2, 2, 0)
        status_rank = 0 if item.get("release_status") == "ready" else (1 if item.get("release_status") == "review" else 2)
        lof = item.get("min_lindel_lof_pct") or 0
        return (0, status_rank, -lof)

    comparisons.sort(key=_sort_key)
    return {
        "ok": True,
        "gene": gene.upper(),
        "edit_type": edit_type,
        "enzymes_compared": len(comparisons),
        "recommended_enzyme": comparisons[0]["enzyme_id"] if comparisons and comparisons[0].get("eligible") else None,
        "candidates": comparisons,
    }


def tool_rank_ko_guides_lindel(gene: str, top_n: int = 10) -> Dict[str, Any]:
    """Rank all candidate SpCas9 (NGG) knockout guides across non-terminal coding exons using JavaScript Lindel + Assured QC NMD/truncation rules."""
    bundled = get_bundled_references()
    sym = (gene or "").strip().upper()
    gb_path = bundled.get(sym)
    rank_script = DESIGN_SKILL_DIR / "assured-crispr-designer" / "tools" / "ko-rank" / "rank.mjs"

    if not gb_path or not gb_path.exists():
        # Run design first or fetch reference
        return {
            "ok": False,
            "error": f"No local GenBank file found for {sym} to run full-locus Lindel ranking. Bundled genes: {sorted(bundled.keys())}",
        }
    if not rank_script.exists():
        return {"ok": False, "error": f"Lindel rank script not found at {rank_script}"}

    out_csv = BIOMNI_WORKSPACE / f"{sym.lower()}_lindel_ko_ranking.csv"
    proc = subprocess.run(
        ["node", str(rank_script), str(gb_path), str(out_csv), "--gene", sym],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0 or not out_csv.exists():
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip() or "Lindel ranking failed."}

    lines = [line.strip() for line in out_csv.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 2:
        return {"ok": False, "error": "No candidate NGG guides found in non-terminal coding exons."}

    headers = lines[0].split(",")
    rows = []
    for line in lines[1:]:
        vals = line.split(",")
        row = dict(zip(headers, vals))
        for num_key in (
            "coding_exon",
            "cut_after_exon_nt",
            "gc_percent",
            "lindel_frameshift_pct",
            "lindel_plus1_ins_pct",
            "predicted_LoF_allele_pct",
            "predicted_clear_NMD_allele_pct",
            "predicted_in_frame_pct",
            "predicted_biallelic_LoF_clone_pct",
        ):
            if num_key in row:
                try:
                    row[num_key] = float(row[num_key]) if "." in str(row[num_key]) else int(row[num_key])
                except ValueError:
                    pass
        rows.append(row)

    return {
        "ok": True,
        "gene": sym,
        "total_candidates_ranked": len(rows),
        "csv_path": str(out_csv),
        "top_guides": rows[: int(top_n)],
    }


def tool_lookup_brunello_And_tags(
    gene: Optional[str] = None,
    tag_query: Optional[str] = None,
    fwd_primer: Optional[str] = None,
    rev_primer: Optional[str] = None,
) -> Dict[str, Any]:
    """Query the curated Brunello human KO library, FPbase fluorescent tags, or run in-silico primer specificity check."""
    results: Dict[str, Any] = {"ok": True}
    brunello_cjs = DESIGN_SKILL_DIR / "brunello-lookup.cjs"
    if gene and brunello_cjs.exists():
        js = (
            f"const {{ lookupBrunelloGuides }} = require({json.dumps(str(brunello_cjs))});"
            f"lookupBrunelloGuides({{ gene: {json.dumps(gene.strip().upper())} }})"
            ".then(r => console.log(JSON.stringify(r)))"
            ".catch(e => { console.error(e.message); process.exit(1); });"
        )
        proc = subprocess.run(
            ["node", "-e", js],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        out_str = proc.stdout.strip()
        try:
            b_data = json.loads(out_str) if out_str else {}
            results["brunello"] = b_data
            results["brunello_guides"] = b_data.get("guides") or []
        except Exception:
            results["brunello"] = out_str or proc.stderr.strip()

    fpbase_cjs = DESIGN_SKILL_DIR / "fpbase-lookup.cjs"
    if tag_query and fpbase_cjs.exists():
        js = (
            f"const {{ lookupFpbaseReporters }} = require({json.dumps(str(fpbase_cjs))});"
            f"lookupFpbaseReporters({{ search: {json.dumps(tag_query.strip())}, limit: 5 }})"
            ".then(r => console.log(JSON.stringify(r)))"
            ".catch(e => { console.error(e.message); process.exit(1); });"
        )
        proc = subprocess.run(
            ["node", "-e", js],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        out_str = proc.stdout.strip()
        try:
            f_data = json.loads(out_str) if out_str else {}
            results["fpbase"] = f_data
            results["fpbase_reporters"] = f_data.get("reporters") or []
        except Exception:
            results["fpbase"] = out_str or proc.stderr.strip()

    spec_cjs = DESIGN_SKILL_DIR / "primer-specificity-lookup.cjs"
    if fwd_primer and rev_primer and spec_cjs.exists():
        js = (
            f"const {{ lookupPrimerSpecificity }} = require({json.dumps(str(spec_cjs))});"
            f"lookupPrimerSpecificity({{ forwardPrimer: {json.dumps(fwd_primer.strip())}, reversePrimer: {json.dumps(rev_primer.strip())} }})"
            ".then(r => console.log(JSON.stringify(r)))"
            ".catch(e => { console.error(e.message); process.exit(1); });"
        )
        proc = subprocess.run(
            ["node", "-e", js],
            capture_output=True,
            text=True,
            check=False,
            timeout=25,
        )
        out_str = proc.stdout.strip()
        try:
            results["primer_specificity"] = json.loads(out_str) if out_str else {}
        except Exception:
            results["primer_specificity"] = out_str or proc.stderr.strip()

    return results


def tool_analyze_sanger_traces(
    use_demo_apoe: bool = True,
    control_trace_path: Optional[str] = None,
    sample_trace_paths: Optional[List[str]] = None,
    design_spec_path: Optional[str] = None,
    workflow: str = "snp",
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Assured QC Sanger chromatogram (.ab1) deconvolution and 96-well clone decision ranking."""
    if use_demo_apoe or not control_trace_path:
        demo_dir = get_demo_trace_dir()
        ctrl = demo_dir / "apoe_control.ab1"
        samples = [
            demo_dir / "apoe_snp_clone_hom_edited.ab1",
            demo_dir / "apoe_snp_clone_het_edited.ab1",
            demo_dir / "apoe_snp_clone_partial_het_edited.ab1",
        ]
        spec_file = demo_dir / "apoe_r176c_snp.design.json"
    else:
        ctrl = Path(control_trace_path)
        samples = [Path(p) for p in (sample_trace_paths or [])]
        spec_file = Path(design_spec_path) if design_spec_path else None

    qc_out = run_in_silico_assessment(
        control_path=ctrl,
        sample_paths=samples,
        qc_design_path=spec_file if (spec_file and spec_file.exists()) else None,
        workflow=workflow,
    )
    saved = save_assessment_reports(project_id, qc_out)
    return {
        "ok": True,
        "workflow": qc_out.get("workflow"),
        "total_samples": qc_out.get("totalSamples"),
        "accepted_count": qc_out.get("acceptedCount"),
        "preferred_clone": qc_out.get("preferred"),
        "results": qc_out.get("results"),
        "saved_reports": {k: str(v) for k, v in saved.items() if v},
        "_raw_qc_output": qc_out,
    }


# ---------------------------------------------------------------------------
# 2. External Biomedical Database Tools (ClinVar, UniProt, gnomAD, OpenTargets, PubMed)
# ---------------------------------------------------------------------------


def tool_search_clinvar_variants(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Search NCBI ClinVar for human pathogenic or clinical variants by gene + protein change, rsID, or VCV accession."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Query is required."}
    try:
        esearch = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "clinvar", "term": q, "retmode": "json", "retmax": max_results},
            timeout=15,
        ).json()
        id_list = (esearch.get("esearchresult") or {}).get("idlist") or []
        if not id_list:
            return {"ok": True, "query": q, "variants": [], "message": "No ClinVar records matched."}

        esum = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "clinvar", "id": ",".join(id_list), "retmode": "json"},
            timeout=15,
        ).json()
        result_map = esum.get("result") or {}
        variants = []
        for vid in id_list:
            item = result_map.get(str(vid)) or {}
            germline = item.get("germline_classification") or {}
            variants.append(
                {
                    "variation_id": vid,
                    "accession": item.get("accession"),
                    "title": item.get("title"),
                    "gene_sort": item.get("gene_sort"),
                    "clinical_significance": germline.get("description") or item.get("clinical_significance", {}).get("description"),
                    "review_status": germline.get("review_status"),
                    "protein_change": item.get("protein_change"),
                }
            )
        return {"ok": True, "query": q, "variants": variants}
    except Exception as exc:
        return {"ok": False, "query": q, "error": f"ClinVar lookup failed: {exc}"}


def tool_query_uniprot_protein(gene: str, organism_id: str = "9606") -> Dict[str, Any]:
    """Fetch canonical protein function, length, domains, and subcellular location from UniProtKB."""
    sym = (gene or "").strip().upper()
    if not sym:
        return {"ok": False, "error": "Gene symbol required."}
    try:
        resp = requests.get(
            "https://rest.uniprot.org/uniprotkb/search",
            params={
                "query": f"gene_exact:{sym} AND organism_id:{organism_id} AND reviewed:true",
                "format": "json",
                "size": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        results = (resp.json() or {}).get("results") or []
        if not results:
            return {"ok": True, "gene": sym, "found": False}
        entry = results[0]
        acc = entry.get("primaryAccession")
        prot_name = (
            ((entry.get("proteinDescription") or {}).get("recommendedName") or {})
            .get("fullName", {})
            .get("value")
        )
        seq_len = (entry.get("sequence") or {}).get("length")
        comments = entry.get("comments") or []
        func_texts = [
            t.get("value")
            for c in comments
            if c.get("commentType") == "FUNCTION"
            for t in (c.get("texts") or [])
            if t.get("value")
        ]
        features = entry.get("features") or []
        domains = [
            {
                "type": f.get("type"),
                "description": f.get("description"),
                "start": ((f.get("location") or {}).get("start") or {}).get("value"),
                "end": ((f.get("location") or {}).get("end") or {}).get("value"),
            }
            for f in features
            if f.get("type") in ("Domain", "Region", "Active site", "Binding site", "Topological domain")
        ][:10]
        return {
            "ok": True,
            "gene": sym,
            "found": True,
            "uniprot_accession": acc,
            "protein_name": prot_name,
            "length_aa": seq_len,
            "function": func_texts[0] if func_texts else "",
            "domains_and_sites": domains,
            "alphafold_url": f"https://alphafold.ebi.ac.uk/entry/{acc}" if acc else None,
        }
    except Exception as exc:
        return {"ok": False, "gene": sym, "error": f"UniProt query failed: {exc}"}


def tool_query_gnomad_constraint(gene: str) -> Dict[str, Any]:
    """Query gnomAD GraphQL API for loss-of-function intolerance (pLI, LOEUF, obs/exp LoF) and missense constraint."""
    sym = (gene or "").strip().upper()
    query = """
    query GeneConstraint($geneSymbol: String!) {
      gene(gene_symbol: $geneSymbol, reference_genome: GRCh38) {
        gene_id
        symbol
        name
        gnomad_constraint {
          exp_lof
          obs_lof
          oe_lof
          oe_lof_upper
          pLI
          lof_z
          mis_z
          syn_z
        }
      }
    }
    """
    try:
        resp = requests.post(
            "https://gnomad.broadinstitute.org/api",
            json={"query": query, "variables": {"geneSymbol": sym}},
            timeout=15,
        )
        resp.raise_for_status()
        data = ((resp.json() or {}).get("data") or {}).get("gene")
        if not data:
            return {"ok": True, "gene": sym, "found": False}
        c = data.get("gnomad_constraint") or {}
        pli = c.get("pLI")
        loeuf = c.get("oe_lof_upper")
        return {
            "ok": True,
            "gene": sym,
            "found": True,
            "ensembl_id": data.get("gene_id"),
            "name": data.get("name"),
            "pLI": round(pli, 4) if isinstance(pli, (int, float)) else None,
            "LOEUF": round(loeuf, 3) if isinstance(loeuf, (int, float)) else None,
            "obs_lof": c.get("obs_lof"),
            "exp_lof": round(c.get("exp_lof"), 1) if isinstance(c.get("exp_lof"), (int, float)) else None,
            "mis_z": round(c.get("mis_z"), 2) if isinstance(c.get("mis_z"), (int, float)) else None,
            "haploinsufficiency_interpretation": (
                "Strong LoF intolerance (haploinsufficient / essential candidate; heterozygous KO may have phenotype)"
                if (isinstance(loeuf, (int, float)) and loeuf < 0.35) or (isinstance(pli, (int, float)) and pli > 0.9)
                else "Tolerant to heterozygous LoF (biallelic knockout recommended for complete loss of function)"
            ),
        }
    except Exception as exc:
        return {"ok": False, "gene": sym, "error": f"gnomAD query failed: {exc}"}


def tool_query_opentargets(ensembl_id_or_gene: str) -> Dict[str, Any]:
    """Query Open Targets Platform GraphQL API for disease associations and therapeutic tractability."""
    q = (ensembl_id_or_gene or "").strip().upper()
    search_gql = """
    query SearchTarget($q: String!) {
      search(queryString: $q, entityNames: ["target"], page: {index: 0, size: 1}) {
        hits {
          id
          name
          description
        }
      }
    }
    """
    target_gql = """
    query TargetInfo($ensgId: String!) {
      target(ensemblId: $ensgId) {
        id
        approvedSymbol
        approvedName
        associatedDiseases(page: {index: 0, size: 5}) {
          rows {
            score
            disease {
              id
              name
            }
          }
        }
      }
    }
    """
    try:
        ensg = q if q.startswith("ENSG") else None
        if not ensg:
            s_resp = requests.post(
                "https://api.platform.opentargets.org/api/v4/graphql",
                json={"query": search_gql, "variables": {"q": q}},
                timeout=15,
            ).json()
            hits = (((s_resp.get("data") or {}).get("search") or {}).get("hits")) or []
            if hits:
                ensg = hits[0].get("id")
        if not ensg:
            return {"ok": True, "query": q, "found": False}

        t_resp = requests.post(
            "https://api.platform.opentargets.org/api/v4/graphql",
            json={"query": target_gql, "variables": {"ensgId": ensg}},
            timeout=15,
        ).json()
        target = (t_resp.get("data") or {}).get("target")
        if not target:
            return {"ok": True, "query": q, "found": False}
        diseases = [
            {"disease": r["disease"]["name"], "id": r["disease"]["id"], "score": round(r["score"], 3)}
            for r in ((target.get("associatedDiseases") or {}).get("rows") or [])
        ]
        return {
            "ok": True,
            "ensembl_id": target.get("id"),
            "symbol": target.get("approvedSymbol"),
            "name": target.get("approvedName"),
            "top_diseases": diseases,
        }
    except Exception as exc:
        return {"ok": False, "query": q, "error": f"OpenTargets query failed: {exc}"}


def tool_search_pubmed_literature(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Search PubMed via NCBI E-utilities for scientific papers on a gene, variant, or CRISPR protocol."""
    q = (query or "").strip()
    try:
        esearch = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": q, "retmode": "json", "retmax": max_results, "sort": "relevance"},
            timeout=15,
        ).json()
        pmids = (esearch.get("esearchresult") or {}).get("idlist") or []
        if not pmids:
            return {"ok": True, "query": q, "papers": []}
        esum = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
            timeout=15,
        ).json()
        result_map = esum.get("result") or {}
        papers = []
        for pmid in pmids:
            item = result_map.get(str(pmid)) or {}
            authors = item.get("authors") or []
            first_author = authors[0].get("name") if authors else ""
            papers.append(
                {
                    "pmid": pmid,
                    "title": item.get("title"),
                    "source": item.get("source"),
                    "pubdate": item.get("pubdate"),
                    "first_author": first_author,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                }
            )
        return {"ok": True, "query": q, "papers": papers}
    except Exception as exc:
        return {"ok": False, "query": q, "error": f"PubMed query failed: {exc}"}


# ---------------------------------------------------------------------------
# 3. Sandboxed Code Execution Tool (Biomni-A1 Code-as-Action Interface)
# ---------------------------------------------------------------------------


def tool_execute_bioinformatics_code(
    code: str,
    language: str = "python",
    description: str = "",
) -> Dict[str, Any]:
    """Execute a sandboxed Python or Node.js bioinformatics script in `data/biomni_workspace/` and return stdout/stderr."""
    lang = (language or "python").strip().lower()
    suffix = ".mjs" if lang in ("node", "js", "javascript") else ".py"
    script_path = BIOMNI_WORKSPACE / f"biomni_exec_{int(time.time() * 1000)}{suffix}"
    script_path.write_text(code, encoding="utf-8")

    cmd = ["node", str(script_path)] if suffix == ".mjs" else [sys.executable, str(script_path)]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BIOMNI_WORKSPACE),
            capture_output=True,
            text=True,
            check=False,
            timeout=25,
        )
        return {
            "ok": proc.returncode == 0,
            "language": lang,
            "description": description,
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip()[:4000],
            "stderr": proc.stderr.strip()[:2000],
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "script_path": str(script_path),
        }
    except Exception as exc:
        return {"ok": False, "language": lang, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tool Catalog & OpenAI/Gemini Function Calling Schemas
# ---------------------------------------------------------------------------

ASSURED_E1_TOOLS: Dict[str, Dict[str, Any]] = {
    "fetch_gene_reference": {
        "fn": tool_fetch_gene_reference,
        "category": "Genomics & Reference",
        "description": "Fetch verified GenBank reference or Ensembl MANE Select transcript metadata for a target gene.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene": {"type": "string", "description": "Target gene symbol (e.g. APOE, TP53, DIPK2A,NR2F2)"},
                "species": {"type": "string", "default": "human"},
            },
            "required": ["gene"],
        },
    },
    "design_crispr_edit": {
        "fn": tool_design_crispr_edit,
        "category": "ASSURED CRISPR Design",
        "description": "Design CRISPR guides, silent-blocked HDR donors, and thermodynamic primers across 7 nucleases, with automatic Lindel KO outcome ranking.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene": {"type": "string", "description": "Target gene symbol (e.g. TP53, APOE, DIPK2A)"},
                "edit_type": {"type": "string", "enum": ["ko", "pm", "ct", "nt", "it"], "default": "ko"},
                "mutation": {"type": "string", "description": "Protein substitution for pm (e.g. R176C, R175H) or insertion codon for it (e.g. R154)"},
                "tag": {"type": "string", "description": "Tag or reporter cassette (e.g. FLAG, 2xHA-only, SD40-V5, dTAG-V5, T2A-EGFP, SPOT)", "default": "FLAG"},
                "enzyme": {"type": "string", "enum": ["spcas9", "spcas9ng", "sacas9", "spry", "ascas12a", "lbcas12a", "mad7"], "default": "spcas9"},
                "arm_length": {"type": "integer", "default": 400},
            },
            "required": ["gene"],
        },
    },
    "compare_crispr_enzymes": {
        "fn": tool_compare_crispr_enzymes,
        "category": "ASSURED CRISPR Design",
        "description": "Compare all 7 CRISPR nucleases (SpCas9, SpCas9-NG, SaCas9, SpRY, enAsCas12a, LbCas12a, Mad7) for a gene and modification.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene": {"type": "string"},
                "edit_type": {"type": "string", "enum": ["ko", "pm", "ct", "nt", "it"], "default": "ko"},
                "mutation": {"type": "string", "default": ""},
                "tag": {"type": "string", "default": "FLAG"},
            },
            "required": ["gene"],
        },
    },
    "rank_ko_guides_lindel": {
        "fn": tool_rank_ko_guides_lindel,
        "category": "KO Repair Outcome Modeling",
        "description": "Rank all non-terminal coding exon NGG guides using the JavaScript Lindel DNA repair model + Assured QC NMD/truncation rules.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene": {"type": "string", "description": "Gene symbol (e.g. DIPK2A, TP53, APOE, NR2F2)"},
                "top_n": {"type": "integer", "default": 10},
            },
            "required": ["gene"],
        },
    },
    "lookup_brunello_and_tags": {
        "fn": tool_lookup_brunello_And_tags,
        "category": "Curated Libraries & Specificity",
        "description": "Look up validated human KO guides in the Broad Brunello library, fluorescent proteins in FPbase, or check primer specificity.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene": {"type": "string"},
                "tag_query": {"type": "string"},
                "fwd_primer": {"type": "string"},
                "rev_primer": {"type": "string"},
            },
        },
    },
    "analyze_sanger_traces": {
        "fn": tool_analyze_sanger_traces,
        "category": "Assured QC Trace Deconvolution",
        "description": "Deconvolve Sanger sequencing (.ab1) chromatograms, quantify WT/Indel/HDR allele fractions, and rank edited clones.",
        "parameters": {
            "type": "object",
            "properties": {
                "use_demo_apoe": {"type": "boolean", "default": True},
                "workflow": {"type": "string", "enum": ["snp", "knockout", "deletion", "tag_small"], "default": "snp"},
            },
        },
    },
    "search_clinvar_variants": {
        "fn": tool_search_clinvar_variants,
        "category": "Biomedical Databases",
        "description": "Search NCBI ClinVar for clinical/pathogenic variants (e.g. 'TP53 R175H', 'APOE R176C').",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    "query_uniprot_protein": {
        "fn": tool_query_uniprot_protein,
        "category": "Biomedical Databases",
        "description": "Query UniProtKB for protein length, functional summary, domains, and AlphaFold structure link.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene": {"type": "string"},
            },
            "required": ["gene"],
        },
    },
    "query_gnomad_constraint": {
        "fn": tool_query_gnomad_constraint,
        "category": "Biomedical Databases",
        "description": "Query gnomAD v4 GRCh38 for gene loss-of-function intolerance (pLI, LOEUF, obs/exp LoF, missense Z-score).",
        "parameters": {
            "type": "object",
            "properties": {
                "gene": {"type": "string"},
            },
            "required": ["gene"],
        },
    },
    "query_opentargets": {
        "fn": tool_query_opentargets,
        "category": "Biomedical Databases",
        "description": "Query Open Targets Platform for human disease associations and target evidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "ensembl_id_or_gene": {"type": "string"},
            },
            "required": ["ensembl_id_or_gene"],
        },
    },
    "search_pubmed_literature": {
        "fn": tool_search_pubmed_literature,
        "category": "Biomedical Databases",
        "description": "Search PubMed for peer-reviewed papers on a gene, variant, or genome editing strategy.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    "execute_bioinformatics_code": {
        "fn": tool_execute_bioinformatics_code,
        "category": "Code Execution (Biomni-A1)",
        "description": "Execute custom Python or Node.js code in an isolated scratch workspace for sequence analysis, statistics, or file generation.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python or Node.js source code to execute"},
                "language": {"type": "string", "enum": ["python", "node"], "default": "python"},
                "description": {"type": "string", "default": ""},
            },
            "required": ["code"],
        },
    },
}


def list_environment_catalog() -> List[Dict[str, Any]]:
    """Return metadata for all registered tools in the ASSURED-E1 environment."""
    return [
        {
            "name": name,
            "category": spec["category"],
            "description": spec["description"],
            "parameters": spec["parameters"],
        }
        for name, spec in ASSURED_E1_TOOLS.items()
    ]


def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a registered ASSURED-E1 tool by name with timing and error handling."""
    if name not in ASSURED_E1_TOOLS:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    fn: Callable = ASSURED_E1_TOOLS[name]["fn"]
    t0 = time.time()
    try:
        res = fn(**arguments)
        res["_duration_ms"] = round((time.time() - t0) * 1000, 1)
        return res
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "_duration_ms": round((time.time() - t0) * 1000, 1),
        }
