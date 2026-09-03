/**
 * Freeze the live API into static JSON for the GitHub Pages demo.
 *
 * Pages serves files; it cannot run FastAPI. Deploying the frontend alone
 * would publish an app that loads and then reports the API unreachable, which
 * is worse than no link at all. So the demo ships a snapshot of real
 * responses, and the client reads those instead of fetching.
 *
 * What the snapshot cannot do is ingest a new file - that needs the pipeline.
 * The UI says so rather than offering a button that fails.
 *
 * Regenerate with the backend running and a dataset loaded:
 *
 *   node scripts/snapshot.mjs
 */

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const API = process.env.API_URL ?? "http://localhost:8000";
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "demo-data");

// The graph is snapshotted whole, not pre-trimmed.
//
// An earlier version captured the 700 highest-risk nodes and filtered from
// there. That silently changed what the demo showed: IP nodes carry no risk
// score, so they sorted last and were cut, and "Medium and up" rendered 128
// nodes where the live API returns 480. Filters are applied client-side
// instead, over the complete graph, so every view matches the real thing.
const ENDPOINTS = [
  ["health.json", "/health"],
  ["alerts.json", "/alerts?limit=5000"],
  ["graph.json", "/graph?min_risk=0&limit=100000&include_ips=true"],
];

// Fields the UI never renders. Dropping them halves the payload - the full
// graph goes from 1.5 MB to 541 KB gzipped - and costs nothing on screen.
const DROP_FROM_LINKS = ["txid", "fee", "timestamp"];
const DROP_FROM_NODES = ["first_seen", "last_seen"];

function slim(body) {
  if (!Array.isArray(body?.nodes) || !Array.isArray(body?.links)) return body;
  for (const link of body.links) for (const k of DROP_FROM_LINKS) delete link[k];
  for (const node of body.nodes) for (const k of DROP_FROM_NODES) delete node[k];
  return body;
}

async function main() {
  await mkdir(OUT, { recursive: true });

  const manifest = {
    generated_at: new Date().toISOString(),
    source: API,
    files: [],
  };

  for (const [name, path] of ENDPOINTS) {
    const response = await fetch(`${API}${path}`);
    if (!response.ok) {
      throw new Error(`${path} -> ${response.status} ${response.statusText}`);
    }
    const body = slim(await response.json());
    const text = JSON.stringify(body);
    await writeFile(join(OUT, name), text, "utf8");

    const kb = Buffer.byteLength(text) / 1024;
    const gz = gzipSync(text).length / 1024;
    manifest.files.push({ name, path, kb: Math.round(kb) });
    console.log(
      `  ${name.padEnd(13)} ${kb.toFixed(0).padStart(6)} KB  (${gz.toFixed(0)} KB gzipped)  <- ${path}`,
    );
  }

  const health = JSON.parse(
    await (await fetch(`${API}/health`)).text(),
  );
  manifest.transactions = health.transactions;
  manifest.wallets_scored = health.wallets_scored;

  await writeFile(
    join(OUT, "manifest.json"),
    JSON.stringify(manifest, null, 2),
    "utf8",
  );

  console.log(
    `\n  snapshot of ${health.transactions.toLocaleString()} transactions / ` +
      `${health.wallets_scored.toLocaleString()} wallets -> public/demo-data/`,
  );
}

main().catch((err) => {
  console.error(`\n  snapshot failed: ${err.message}`);
  console.error(`  Is the backend running at ${API} with a dataset ingested?`);
  process.exit(1);
});
