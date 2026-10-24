#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const BENCH_ROOT = path.resolve("c:/Users/ntelugu/Desktop/assured-bench");

const designEngineUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/designEngine.js")).href;
const qcExportUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/qcExport.js")).href;
const designSchemeUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/designScheme.js")).href;
const designExportUrl = pathToFileURL(path.join(BENCH_ROOT, "src/services/designExport.js")).href;
const ensemblUrl = pathToFileURL(path.join(BENCH_ROOT, "src/services/ensembl.js")).href;
const tp53RefPath = path.join(BENCH_ROOT, "public/references/tp53-NG_017013.2.gb");

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

async function main() {
  try {
    const input = await readInput();
    const gene = (input.gene || "").trim();
    const editType = (input.editType || "ko").toLowerCase();
    const mutation = (input.mutation || "").trim();
    const tag = (input.tag || "FLAG").trim();
    const armLength = Number(input.armLength) || 400;
    const options = {
      deliveryMethod: "rnp",
      maxBlockingChanges: 3,
      ensureGuidePair: true,
      allowNonCodingBlockers: true,
      expectedGene: gene,
      ...(input.options || {})
    };

    let genbankText = (input.genbankText || "").trim();

    const { runDesign } = await import(designEngineUrl);
    const { buildQcDesignSpec } = await import(qcExportUrl);
    const { buildDesignSchemeSvg } = await import(designSchemeUrl);
    const { generateReportHtml } = await import(designExportUrl);

    if (!genbankText) {
      if (gene.toUpperCase() === "TP53" && fs.existsSync(tp53RefPath)) {
        genbankText = fs.readFileSync(tp53RefPath, "utf-8");
      } else if (gene) {
        try {
          const { fetchGeneGenbank } = await import(ensemblUrl);
          const ensemblRes = await fetchGeneGenbank(gene);
          genbankText = ensemblRes.genbankText || "";
        } catch (fetchErr) {
          if (fs.existsSync(tp53RefPath) && gene.toUpperCase() === "TP53") {
            genbankText = fs.readFileSync(tp53RefPath, "utf-8");
          } else {
            throw new Error(`Could not fetch reference for gene '${gene}': ${fetchErr.message}`);
          }
        }
      }
    }

    if (!genbankText) {
      throw new Error("No GenBank reference provided or found for target gene.");
    }

    const designResult = runDesign(editType, genbankText, mutation, tag, armLength, options);
    if (!designResult || designResult.err) {
      throw new Error(designResult?.err || "Design engine failed to generate candidates.");
    }

    const qcDesignSpec = buildQcDesignSpec(designResult);
    const svg = buildDesignSchemeSvg(designResult);
    const project = {
      gene: gene || designResult.gene,
      editType: editType || designResult.type,
      mutation,
      tag,
      options
    };
    const reportHtml = generateReportHtml(project, designResult);

    const output = {
      success: true,
      gene: project.gene,
      editType: project.editType,
      designResult,
      qcDesignSpec,
      svg,
      reportHtml
    };

    process.stdout.write(JSON.stringify(output));
  } catch (err) {
    const errorOutput = {
      success: false,
      error: err.message || String(err)
    };
    process.stdout.write(JSON.stringify(errorOutput));
    process.exitCode = 1;
  }
}

main();