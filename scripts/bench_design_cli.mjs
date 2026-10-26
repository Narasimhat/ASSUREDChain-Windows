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
    if (fs.existsSync(path.join(candidate, "src/engine/design/designEngine.js"))) {
      return path.resolve(candidate);
    }
  }
  return path.resolve(CHAIN_ROOT, "../assured-bench");
}

const BENCH_ROOT = resolveBenchRoot();

const designEngineUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/designEngine.js")).href;
const qcExportUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/qcExport.js")).href;
const designSchemeUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/designScheme.js")).href;
const designExportUrl = pathToFileURL(path.join(BENCH_ROOT, "src/services/designExport.js")).href;
const ensemblUrl = pathToFileURL(path.join(BENCH_ROOT, "src/services/ensembl.js")).href;
const geneFetchUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/geneFetch.js")).href;
const enzymeConfigUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/enzymeConfig.js")).href;
const loadLindelUrl = pathToFileURL(path.join(BENCH_ROOT, "src/engine/design/lindel/loadLindel.js")).href;

function findBundledReference(gene) {
  if (!gene) return null;
  const sym = gene.trim().toUpperCase();
  const searchDirs = [
    path.join(CHAIN_ROOT, "data/references"),
    path.join(BENCH_ROOT, "public/references"),
    path.join(BENCH_ROOT, "test/fixtures"),
  ];

  for (const dir of searchDirs) {
    if (!fs.existsSync(dir)) continue;
    const files = fs.readdirSync(dir);
    // 1. Exact or prefix match (e.g. apoe-*.gb, tp53-*.gb)
    for (const f of files) {
      if (!f.endsWith(".gb") && !f.endsWith(".genbank")) continue;
      const lower = f.toLowerCase();
      const symLower = sym.toLowerCase();
      if (lower.startsWith(`${symLower}-`) || lower.startsWith(`${symLower}_`) || lower === `${symLower}.gb`) {
        return path.join(dir, f);
      }
    }
    // 2. Substring match
    for (const f of files) {
      if (!f.endsWith(".gb") && !f.endsWith(".genbank")) continue;
      if (f.toLowerCase().includes(sym.toLowerCase())) {
        return path.join(dir, f);
      }
    }
  }
  return null;
}

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

    const { runDesign, summarizeProcurementReadiness } = await import(designEngineUrl);
    const { buildQcDesignSpec } = await import(qcExportUrl);
    const { buildDesignSchemeSvg } = await import(designSchemeUrl);
    const { generateReportHtml } = await import(designExportUrl);

    if (editType === "ko" && (!options.enzyme || options.enzyme === "spcas9")) {
      try {
        const { loadLindel } = await import(loadLindelUrl);
        options.koLindel = await loadLindel();
      } catch (lindelErr) {
        options.koOutcomesUnavailable = `the Lindel weights could not be loaded (${lindelErr?.message || lindelErr}).`;
      }
    }

    if (!genbankText) {
      const bundledPath = findBundledReference(gene);
      if (bundledPath && fs.existsSync(bundledPath)) {
        genbankText = fs.readFileSync(bundledPath, "utf-8");
      } else if (gene) {
        try {
          const { fetchGeneReference } = await import(geneFetchUrl);
          const ref = await fetchGeneReference({ query: gene, source: input.source || "ensembl", species: input.species || "human" });
          genbankText = ref.genbank || "";
        } catch (primaryErr) {
          try {
            const { fetchGeneGenbank } = await import(ensemblUrl);
            const ensemblRes = await fetchGeneGenbank(gene);
            genbankText = ensemblRes.genbankText || "";
          } catch (fetchErr) {
            const fallbackPath = findBundledReference(gene);
            if (fallbackPath && fs.existsSync(fallbackPath)) {
              genbankText = fs.readFileSync(fallbackPath, "utf-8");
            } else {
              throw new Error(`Could not fetch reference for gene '${gene}': ${primaryErr.message || fetchErr.message}. You can upload or select a GenBank (.gb) reference file in Stage 2.`);
            }
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
    designResult.procurement = summarizeProcurementReadiness(designResult);

    const qcDesignSpec = buildQcDesignSpec(designResult);
    const { getEnzyme } = await import(enzymeConfigUrl);
    const enzymeConfig = getEnzyme(options.enzyme || "spcas9");
    if (qcDesignSpec.design) qcDesignSpec.design.enzyme = enzymeConfig;
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