import { useCallback, useEffect, useRef, useState } from "react";
import Dashboard from "./components/Dashboard";
import GraphView from "./components/GraphView";
import WalletPanel from "./components/WalletPanel";
import { IS_STATIC, getHealth, ingest } from "./lib/api";

const VIEWS = [
  { id: "graph", label: "Link analysis" },
  { id: "dashboard", label: "Dashboard" },
];

export default function App() {
  const [view, setView] = useState("graph");
  const [selected, setSelected] = useState(null);
  const [health, setHealth] = useState(null);
  // Bumped after an ingest so both views refetch from one signal, instead of
  // each keeping its own idea of whether the data has changed.
  const [reloadKey, setReloadKey] = useState(0);

  const refreshHealth = useCallback(
    () => getHealth().then(setHealth).catch(() => setHealth(null)),
    [],
  );

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth, reloadKey]);

  const handleIngested = useCallback(() => {
    setSelected(null);
    setReloadKey((k) => k + 1);
  }, []);

  return (
    <div className="flex h-full flex-col bg-ground">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-edge bg-surface px-5 py-3">
        <div>
          <h1 className="text-sm font-semibold tracking-tight text-ink">
            Bitcoin Transaction Threat Monitor
          </h1>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-3">
            SIH 2026 · link analysis and wallet risk scoring
          </p>
        </div>

        <nav className="flex rounded-lg border border-edge bg-surface-2 p-0.5">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              onClick={() => setView(v.id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                view === v.id
                  ? "bg-accent text-ground"
                  : "text-ink-2 hover:text-ink"
              }`}
            >
              {v.label}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-4">
          <HealthPill health={health} />
          {IS_STATIC ? <StaticNote /> : <IngestButton onIngested={handleIngested} />}
        </div>
      </header>

      <main className="min-h-0 flex-1">
        {view === "graph" ? (
          <GraphView
            selected={selected}
            onSelect={setSelected}
            reloadKey={reloadKey}
          />
        ) : (
          <Dashboard
            health={health}
            selected={selected}
            onSelect={setSelected}
            reloadKey={reloadKey}
          />
        )}
      </main>

      <WalletPanel
        address={selected}
        onClose={() => setSelected(null)}
        onSelect={setSelected}
      />
    </div>
  );
}

function StaticNote() {
  return (
    <span className="rounded-md border border-edge bg-surface-2 px-3 py-1.5 text-[11px] text-ink-3">
      Static demo — a frozen snapshot.{" "}
      <a
        href="https://github.com/Harit117/bitcoin-threat-monitor#running-locally"
        target="_blank"
        rel="noreferrer"
        className="text-accent underline decoration-accent/40 underline-offset-2"
      >
        Run it locally
      </a>{" "}
      to ingest your own data.
    </span>
  );
}

function HealthPill({ health }) {
  if (!health) {
    return (
      <span className="flex items-center gap-2 font-mono text-[11px] text-risk-critical">
        <span className="size-1.5 rounded-full bg-risk-critical" />
        API unreachable
      </span>
    );
  }

  // Two different reasons the dashboard can be empty, and they need different
  // fixes: train the models, or upload a dataset. Saying which saves a demo.
  if (!health.models_loaded) {
    return (
      <span className="flex items-center gap-2 font-mono text-[11px] text-risk-high">
        <span className="size-1.5 rounded-full bg-risk-high" />
        Models not trained
      </span>
    );
  }

  if (!health.graph_loaded) {
    return (
      <span className="flex items-center gap-2 font-mono text-[11px] text-ink-3">
        <span className="size-1.5 rounded-full bg-ink-3" />
        No dataset loaded
      </span>
    );
  }

  return (
    <span className="flex items-center gap-2 font-mono text-[11px] text-ink-3">
      <span className="size-1.5 rounded-full bg-risk-low" />
      {health.transactions.toLocaleString()} tx ·{" "}
      {health.wallets_scored.toLocaleString()} wallets
    </span>
  );
}

function IngestButton({ onIngested }) {
  const inputRef = useRef(null);
  const [status, setStatus] = useState({ state: "idle" });

  const upload = async (file) => {
    if (!file) return;
    setStatus({ state: "working", name: file.name });
    try {
      const summary = await ingest(file);
      setStatus({ state: "done", summary });
      onIngested();
      setTimeout(() => setStatus({ state: "idle" }), 6000);
    } catch (err) {
      setStatus({ state: "error", message: err.message });
    }
  };

  return (
    <div className="flex items-center gap-3">
      {status.state === "working" && (
        <span className="flex items-center gap-2 text-[11px] text-ink-2">
          <span className="inline-block size-3 animate-spin rounded-full border-2 border-edge-strong border-t-accent" />
          Processing {status.name}…
        </span>
      )}
      {status.state === "done" && (
        <span className="font-mono text-[11px] text-risk-low">
          {status.summary.transactions.toLocaleString()} tx ·{" "}
          {status.summary.wallets_scored.toLocaleString()} scored ·{" "}
          {status.summary.high_risk_count} high risk ·{" "}
          {status.summary.duration_seconds}s
        </span>
      )}
      {status.state === "error" && (
        <span
          className="max-w-xs truncate text-[11px] text-risk-critical"
          title={status.message}
        >
          {status.message}
        </span>
      )}

      <input
        ref={inputRef}
        type="file"
        accept=".json,.csv,.xml"
        className="hidden"
        onChange={(e) => {
          upload(e.target.files?.[0]);
          // Reset, so re-uploading the same file still fires a change event.
          e.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={status.state === "working"}
        className="rounded-md border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent transition hover:bg-accent/20 disabled:opacity-50"
      >
        Upload dataset
      </button>
    </div>
  );
}
