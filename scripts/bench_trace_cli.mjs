#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CHAIN_ROOT = path.resolve(__dirname, "..");

function resolveBenchRoot() {
  const candidates = [
    process.env.ASSURED_BENCH_ROOT,
    path.resolve(CHAIN_ROOT, "../assured-bench"),
    path.resolve(CHAIN_ROOT, "../Assured_Studio"),
    "c:/Users/ntelugu/Desktop/assured-bench",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, "src/engine/qc/abif.js"))) {
      return path.resolve(candidate);
    }
  }
  return path.resolve(CHAIN_ROOT, "../assured-bench");
}

const BENCH_ROOT = resolveBenchRoot();

const abifUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/qc/abif.js")).href;
const batchUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/qc/batch.js")).href;
const decisionUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/qc/decision.js")).href;
const recommendUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/qc/recommend.js")).href;
const selectionFigureUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/qc/selectionFigure.js")).href;
const reportUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/qc/report.js")).href;
const quickUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/qc/quick.js")).href;

async function readInput() {
  const args = process.argv.slice(2);
  if (args.length > 0) {
    if (args[0] === "--file" && args[1]) {
      return JSON.parse(fs.readFileSync(args[1], "utf-8"));
    }
    return JSON.parse(args[0]);
  }
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
}

function toArrayBuffer(buf) {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

async function main() {
  try {
    const input = await readInput();
    const { readDesign, reportDonors } = await import(quickUrl);

    // 0. Dedicated parse_design action for real-time frontend inspection
    if (input.action === "parse_design") {
      let text = input.text || "";
      if (!text && input.path && fs.existsSync(input.path)) {
        text = fs.readFileSync(input.path, "utf-8");
      }
      if (!text) {
        throw new Error("No design file content or path provided to parse.");
      }
      const d = readDesign(text);
      if (d.kind === "spec") {
        const guides = (d.spec.guides || []).map((g, idx) => ({
          name: g.name || `Guide ${idx + 1}`,
          sequence: g.spacer || "",
          pam: g.pam || "",
          length: (g.spacer || "").length,
        }));
        const donors = (d.spec.donors || []).map((dn, idx) => ({
          name: dn.name || `Donor ${idx + 1}`,
          sequence: dn.sequence || "",
          length: (dn.sequence || "").length,
        }));
        process.stdout.write(JSON.stringify({
          success: true,
          kind: "spec",
          label: d.label || d.spec.design?.label || "Design Spec",
          gene: d.spec.design?.gene || "",
          nuclease: d.spec.design?.nuclease || "SpCas9",
          editKind: d.spec.design?.editKind || (donors.length ? "snp" : (guides.length > 1 ? "deletion" : "knockout")),
          guides,
          donors,
        }));
        return;
      } else if (d.kind === "report") {
        const chosen = d.chosen || {};
        const guides = (chosen.guides || []).map((g, idx) => ({
          name: g.name || `Guide ${idx + 1}`,
          sequence: g.sequence || "",
          length: (g.sequence || "").length,
        }));
        const donors = (chosen.donors || []).map((dn, idx) => ({
          name: dn.name || `ssODN ${idx + 1}`,
          sequence: dn.sequence || "",
          length: (dn.sequence || "").length,
          recommended: dn.recommended,
        }));
        process.stdout.write(JSON.stringify({
          success: true,
          kind: "report",
          label: d.label || chosen.title || "Design Report",
          gene: chosen.gene || "",
          nuclease: d.nuclease || "SpCas9",
          editKind: donors.length > 0 ? "snp" : (guides.length > 1 ? "deletion" : "knockout"),
          guides,
          donors,
        }));
        return;
      } else {
        throw new Error("Could not parse design file. Ensure it is a valid Assured HTML report or JSON spec.");
      }
    }

    const { parseAbif } = await import(abifUrl);
    const { analyseSample, planDesign } = await import(batchUrl);
    const { decide } = await import(decisionUrl);
    const { recommendClones } = await import(recommendUrl);
    const { cloneSelectionFigure } = await import(selectionFigureUrl);
    const { buildResultsCsv, buildQcReportHtml } = await import(reportUrl);

    // 1. Resolve Control Trace
    const controlFile = input.controlFile;
    if (!controlFile || !fs.existsSync(controlFile)) {
      throw new Error(`Control trace file not found: ${controlFile}`);
    }
    const ctrlBuf = fs.readFileSync(controlFile);
    const ctrlName = path.basename(controlFile);
    const control = parseAbif(toArrayBuffer(ctrlBuf), ctrlName);

    // 2. Resolve Sample Traces
    const sampleFiles = Array.isArray(input.sampleFiles) ? input.sampleFiles : [input.sampleFiles].filter(Boolean);
    if (!sampleFiles.length) {
      throw new Error("At least one edited sample trace file must be provided.");
    }

    // 3. Resolve QC Design Spec (JSON Spec, HTML Report, or Manual Sequences)
    let spec = input.qcDesignSpec || null;
    let designFromHtml = null;

    if (!spec && input.qcDesignHtml) {
      designFromHtml = readDesign(input.qcDesignHtml);
    } else if (!spec && input.qcDesignPath && fs.existsSync(input.qcDesignPath)) {
      const ext = path.extname(input.qcDesignPath).toLowerCase();
      const content = fs.readFileSync(input.qcDesignPath, "utf-8");
      if (ext === ".html" || ext === ".htm" || content.trim().startsWith("<")) {
        designFromHtml = readDesign(content);
      } else {
        spec = JSON.parse(content);
      }
    }

    if (designFromHtml) {
      if (designFromHtml.kind === "spec") {
        spec = designFromHtml.spec;
      } else if (designFromHtml.kind === "report") {
        const guides = input.guides && input.guides.length ? input.guides : (designFromHtml.guides || []);
        const donor = input.donor ? input.donor : reportDonors(designFromHtml.donors || []);
        const plan = planDesign({
          control,
          guides,
          donor,
          gene: input.gene || designFromHtml.chosen?.gene || designFromHtml.label || "sample",
          nuclease: input.nuclease || designFromHtml.nuclease || "SpCas9"
        });
        if (plan.error) {
          throw new Error(`Could not construct QC design spec from report: ${plan.error}`);
        }
        spec = plan.spec;
      }
    }

    if (!spec) {
      // Build spec from control trace + manual guides + donor
      const guides = input.guides || [];
      const donor = input.donor || "";
      const plan = planDesign({
        control,
        guides,
        donor,
        gene: input.gene || "sample",
        nuclease: input.nuclease || "SpCas9"
      });
      if (plan.error) {
        throw new Error(`Could not construct QC design spec: ${plan.error}`);
      }
      spec = plan.spec;
    }

    // Determine workflow
    let workflow = input.workflow;
    if (!workflow) {
      const editKind = spec?.design?.editKind;
      if (editKind) {
        workflow = editKind;
      } else if (spec?.donors && spec.donors.length > 0) {
        workflow = "snp";
      } else if (spec?.guides && spec.guides.length > 1) {
        workflow = "deletion";
      } else {
        workflow = "knockout";
      }
    }

    const options = {
      sampleType: "clone",
      goal: "homozygous",
      ...(input.options || {})
    };

    // 4. Analyse each sample trace
    const results = [];
    for (const sFile of sampleFiles) {
      if (!fs.existsSync(sFile)) {
        continue;
      }
      const sBuf = fs.readFileSync(sFile);
      const sName = path.basename(sFile);
      const edited = parseAbif(toArrayBuffer(sBuf), sName);

      const sampleResult = analyseSample({
        name: sName.replace(/\.(ab1|abi|scf)$/i, ""),
        spec,
        control,
        edited,
        sampleType: options.sampleType,
        options
      });

      sampleResult.workflow = workflow;
      sampleResult.decision = decide(workflow, sampleResult, options.decisionOptions || {});
      results.push(sampleResult);
    }

    if (!results.length) {
      throw new Error("None of the specified sample files could be analysed.");
    }

    // 5. Rank Clones
    const recommendations = recommendClones(results, options.recommendOptions || {});
    results.forEach((r, idx) => {
      r.recommendation = recommendations[idx] || null;
    });

    // 6. Find preferred clone
    // Prefer accept status with lowest rank, else lowest rank overall, else first
    const accepted = results.filter((r) => r.ok && r.decision?.decision === "accept");
    let preferred = null;
    if (accepted.length > 0) {
      accepted.sort((a, b) => ((a.recommendation?.rank || 999) - (b.recommendation?.rank || 999)));
      preferred = accepted[0];
    } else {
      const valid = results.filter((r) => r.ok);
      if (valid.length > 0) {
        valid.sort((a, b) => ((a.recommendation?.rank || 999) - (b.recommendation?.rank || 999)));
        preferred = valid[0];
      } else {
        preferred = results[0];
      }
    }

    // 7. Generate Reports
    const screeningCsv = buildResultsCsv(workflow, results);
    let selectionFigureSvg = "";
    try {
      selectionFigureSvg = cloneSelectionFigure(results, {
        title: input.title || `Clone Selection for ${spec?.design?.gene || "Target Locus"}`
      });
    } catch (figErr) {
      selectionFigureSvg = "";
    }

    let reportHtml = "";
    try {
      reportHtml = buildQcReportHtml({
        title: input.title || `Sanger Trace QC & Clone Screening Report - ${spec?.design?.gene || "Target Locus"}`,
        workflow,
        design: spec?.design?.gene || "",
        results,
        generatedAt: new Date(),
        notes: input.notes || []
      });
    } catch (htmlErr) {
      reportHtml = "";
    }

    // Summarized results table for lightweight JSON
    const summaryTable = results.map((r) => ({
      name: r.name,
      ok: r.ok,
      decision: r.decision?.decision || "re-sequence",
      reasons: r.decision?.reasons || [],
      next: r.decision?.next || "",
      wtPct: r.summary?.wtPct ?? null,
      editedPct: r.summary?.editedPct ?? null,
      indelPct: r.summary?.indelPct ?? null,
      betweenCutsPct: r.summary?.betweenCutsPct ?? null,
      intendedEditPct: r.summary?.intendedEditPct ?? null,
      partialConversionPct: r.summary?.partialConversionPct ?? null,
      koScorePct: r.summary?.koScorePct ?? null,
      unexplainedPct: r.summary?.unexplainedPct ?? null,
      genotypeSummary: r.genotype?.summary || "",
      rank: r.recommendation?.rank ?? null,
      score: r.recommendation?.score ?? null,
      priority: r.recommendation?.priority || "",
      action: r.recommendation?.action || "",
      warnings: r.warnings || []
    }));

    const output = {
      success: true,
      workflow,
      totalSamples: results.length,
      acceptedCount: accepted.length,
      preferredSample: preferred.name,
      preferred: {
        name: preferred.name,
        decision: preferred.decision?.decision || "re-sequence",
        reasons: preferred.decision?.reasons || [],
        next: preferred.decision?.next || "",
        wtPct: preferred.summary?.wtPct ?? 0,
        editedPct: preferred.summary?.editedPct ?? 0,
        indelPct: preferred.summary?.indelPct ?? 0,
        intendedEditPct: preferred.summary?.intendedEditPct ?? 0,
        koScorePct: preferred.summary?.koScorePct ?? 0,
        rank: preferred.recommendation?.rank ?? 1,
        priority: preferred.recommendation?.priority || "",
        action: preferred.recommendation?.action || ""
      },
      results: summaryTable,
      screeningCsv,
      selectionFigureSvg,
      reportHtml,
      qcSpec: spec
    };

    console.log(JSON.stringify(output));
  } catch (err) {
    console.error(JSON.stringify({ success: false, error: err.message, stack: err.stack }));
    process.exit(1);
  }
}

main();