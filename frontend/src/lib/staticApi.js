/**
 * The static-demo backend.
 *
 * GitHub Pages cannot run FastAPI, so the published demo reads a frozen
 * snapshot of real API responses instead. Everything read-only behaves as it
 * does against the live server; ingestion does not, because there is no
 * pipeline to run, and the UI says so rather than offering a button that fails.
 *
 * The one piece of real work here is the subgraph. Storing a neighbourhood per
 * wallet would have meant 1,352 files; recomputing it from the graph the page
 * already holds is both smaller and faster. `walk` mirrors the server's
 * get_subgraph, including its most important detail: traversal ignores edge
 * direction, because an investigator expanding a node needs to see who funded
 * it, not only who it paid.
 */

const BASE = `${import.meta.env.BASE_URL ?? "/"}demo-data`;

let cache = null;

async function load() {
  if (cache) return cache;

  const [health, alerts, graph] = await Promise.all(
    ["health.json", "alerts.json", "graph.json"].map(async (name) => {
      const response = await fetch(`${BASE}/${name}`);
      if (!response.ok) {
        throw new Error(`Demo data missing: ${name} (${response.status})`);
      }
      return response.json();
    }),
  );

  const alertsByWallet = new Map(alerts.map((a) => [a.wallet_address, a]));

  // Undirected adjacency, built once, so repeated node clicks are cheap.
  const adjacency = new Map();
  const link = (a, b) => {
    if (!adjacency.has(a)) adjacency.set(a, new Set());
    adjacency.get(a).add(b);
  };
  for (const l of graph.links) {
    const source = typeof l.source === "object" ? l.source.id : l.source;
    const target = typeof l.target === "object" ? l.target.id : l.target;
    link(source, target);
    link(target, source);
  }

  cache = { health, alerts, graph, alertsByWallet, adjacency };
  return cache;
}

const SEVERITY_ORDER = ["low", "medium", "high", "critical"];

export async function getHealth() {
  const { health } = await load();
  return health;
}

export async function getAlerts({ minSeverity, limit = 500 } = {}) {
  const { alerts } = await load();
  let selected = alerts;
  if (minSeverity) {
    const floor = SEVERITY_ORDER.indexOf(minSeverity);
    selected = selected.filter(
      (a) => SEVERITY_ORDER.indexOf(a.severity) >= floor,
    );
  }
  return selected.slice(0, limit);
}

export async function getGraph({
  minRisk = 0,
  limit = 600,
  includeIps = true,
} = {}) {
  const { graph } = await load();

  let nodes = graph.nodes;
  if (!includeIps) nodes = nodes.filter((n) => n.type !== "ip");
  if (minRisk > 0) {
    nodes = nodes.filter(
      (n) => n.type === "ip" || (n.risk_score ?? 0) >= minRisk,
    );
  }

  let keep = new Set(nodes.map((n) => n.id));
  let links = graph.links.filter(
    (l) => keep.has(l.source) && keep.has(l.target),
  );

  if (minRisk > 0 || !includeIps) {
    // Drop IP nodes left connecting nothing, exactly as the server does.
    const connected = new Set();
    for (const l of links) {
      connected.add(l.source);
      connected.add(l.target);
    }
    nodes = nodes.filter((n) => n.type !== "ip" || connected.has(n.id));
    keep = new Set(nodes.map((n) => n.id));
    links = links.filter((l) => keep.has(l.source) && keep.has(l.target));
  }

  if (nodes.length > limit) {
    nodes = [...nodes]
      .sort((a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0))
      .slice(0, limit);
    keep = new Set(nodes.map((n) => n.id));
    links = links.filter((l) => keep.has(l.source) && keep.has(l.target));
  }

  return {
    nodes,
    links,
    total_nodes: graph.total_nodes,
    total_links: graph.total_links,
    truncated:
      nodes.length < graph.total_nodes || links.length < graph.total_links,
  };
}

export async function getWallet(address, { hops = 2, limit = 300 } = {}) {
  const { graph, alertsByWallet, adjacency } = await load();
  const alert = alertsByWallet.get(address);
  if (!alert) {
    const error = new Error(`Unknown wallet: ${address}`);
    error.status = 404;
    throw error;
  }

  // Breadth-first to `hops`, ignoring direction.
  const seen = new Set([address]);
  let frontier = [address];
  for (let depth = 0; depth < hops; depth += 1) {
    const next = [];
    for (const node of frontier) {
      for (const neighbour of adjacency.get(node) ?? []) {
        if (!seen.has(neighbour)) {
          seen.add(neighbour);
          next.push(neighbour);
        }
      }
    }
    frontier = next;
  }

  const nodes = graph.nodes.filter((n) => seen.has(n.id)).slice(0, limit);
  const keep = new Set(nodes.map((n) => n.id));
  const links = graph.links.filter(
    (l) => keep.has(l.source) && keep.has(l.target),
  );

  return {
    ...alert,
    hops,
    subgraph: {
      nodes,
      links,
      total_nodes: seen.size,
      total_links: links.length,
      truncated: nodes.length < seen.size,
    },
  };
}

export function ingest() {
  return Promise.reject(
    new Error(
      "This is a static demo — ingestion needs the Python backend. " +
        "Clone the repo and run it locally to upload your own dataset.",
    ),
  );
}

export const clearGraph = () =>
  Promise.reject(new Error("Not available in the static demo."));
