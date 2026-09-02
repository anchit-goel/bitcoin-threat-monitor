import { useEffect, useState } from "react";
import { getWallet } from "../lib/api";
import {
  SEVERITY_META,
  formatPercent,
  formatScore,
  riskColor,
  truncateAddress,
} from "../lib/risk";

/**
 * The wallet detail panel.
 *
 * Deliberately one component shared by both views. The brief asks the graph
 * and the dashboard to open "the same wallet detail experience", and two
 * panels that drift apart would be the obvious way to fail that - the graph's
 * version showing reasons the table's does not.
 */
export default function WalletPanel({ address, onClose, onSelect }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!address) return undefined;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);

    getWallet(address)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      // A fast click through several nodes must not let an earlier, slower
      // response overwrite the one the user is actually looking at.
      cancelled = true;
    };
  }, [address]);

  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!address) return null;

  const severity = detail ? SEVERITY_META[detail.severity] : null;

  return (
    <aside
      className="fixed inset-y-0 right-0 z-30 flex w-full max-w-md flex-col border-l border-edge bg-surface shadow-2xl"
      aria-label="Wallet detail"
    >
      <header className="flex items-start justify-between gap-3 border-b border-edge px-5 py-4">
        <div className="min-w-0">
          <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
            Wallet
          </p>
          <p className="mt-1 truncate font-mono text-sm text-ink" title={address}>
            {address}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-md border border-edge px-2 py-1 text-xs text-ink-2 transition hover:border-edge-strong hover:text-ink"
        >
          Close
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-5">
        {loading && <PanelSkeleton />}

        {error && (
          <p className="rounded-md border border-risk-critical/40 bg-risk-critical/10 px-3 py-2 text-sm text-risk-critical">
            {error}
          </p>
        )}

        {detail && (
          <div className="flex flex-col gap-6">
            <section className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-edge bg-edge">
              <Metric
                label="Risk"
                value={formatScore(detail.risk_score)}
                color={riskColor(detail.risk_score)}
              />
              <Metric label="Confidence" value={formatPercent(detail.confidence)} />
              <div className="flex flex-col gap-1 bg-surface-2 px-3 py-3">
                <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-ink-3">
                  Severity
                </span>
                <span
                  className={`inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-xs font-medium ${severity.chip}`}
                >
                  {severity.label}
                </span>
              </div>
            </section>

            <section>
              <h3 className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
                Why it was flagged
              </h3>
              <ol className="mt-3 flex flex-col gap-3">
                {detail.top_reasons.map((reason, index) => (
                  <li
                    key={reason}
                    className="flex gap-3 rounded-md border border-edge bg-surface-2 px-3 py-2.5 text-sm leading-relaxed text-ink-2"
                  >
                    <span className="mt-0.5 font-mono text-xs text-ink-3">
                      {index + 1}
                    </span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ol>
            </section>

            <section>
              <h3 className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
                Connected wallets ({detail.connected_wallets.length})
              </h3>
              {detail.connected_wallets.length === 0 ? (
                <p className="mt-2 text-sm text-ink-3">No direct counterparties.</p>
              ) : (
                <ul className="mt-3 flex flex-wrap gap-1.5">
                  {detail.connected_wallets.map((neighbour) => (
                    <li key={neighbour}>
                      <button
                        type="button"
                        onClick={() => onSelect?.(neighbour)}
                        title={neighbour}
                        className="rounded border border-edge bg-surface-2 px-2 py-1 font-mono text-[11px] text-ink-2 transition hover:border-accent hover:text-accent"
                      >
                        {truncateAddress(neighbour)}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <h3 className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">
                Neighbourhood
              </h3>
              <p className="mt-2 text-sm text-ink-2">
                {detail.subgraph.nodes.length} nodes and{" "}
                {detail.subgraph.links.length} links within {detail.hops} hops
                {detail.subgraph.truncated && ", trimmed to fit"}.
              </p>
            </section>
          </div>
        )}
      </div>
    </aside>
  );
}

function Metric({ label, value, color }) {
  return (
    <div className="flex flex-col gap-1 bg-surface-2 px-3 py-3">
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-ink-3">
        {label}
      </span>
      <span
        className="font-mono text-lg tabular-nums"
        style={color ? { color } : undefined}
      >
        {value}
      </span>
    </div>
  );
}

function PanelSkeleton() {
  return (
    <div className="flex animate-pulse flex-col gap-4">
      <div className="h-16 rounded-lg bg-surface-2" />
      <div className="h-4 w-32 rounded bg-surface-2" />
      <div className="h-14 rounded bg-surface-2" />
      <div className="h-14 rounded bg-surface-2" />
    </div>
  );
}
