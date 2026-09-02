import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getAlerts } from "../lib/api";
import {
  SEVERITIES,
  SEVERITY_META,
  formatPercent,
  formatScore,
  riskColor,
  severityCounts,
  truncateAddress,
} from "../lib/risk";

const SORTS = {
  risk_score: (a, b) => b.risk_score - a.risk_score,
  confidence: (a, b) => b.confidence - a.confidence,
  severity: (a, b) =>
    SEVERITIES.indexOf(b.severity) - SEVERITIES.indexOf(a.severity) ||
    b.risk_score - a.risk_score,
  wallet_address: (a, b) => a.wallet_address.localeCompare(b.wallet_address),
};

export default function Dashboard({ health, selected, onSelect, reloadKey }) {
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [severityFilter, setSeverityFilter] = useState("all");
  const [sortKey, setSortKey] = useState("risk_score");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getAlerts({ limit: 400 })
      .then((data) => {
        if (!cancelled) setAlerts(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setAlerts([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // Every figure on this page is derived from the alerts actually fetched, so
  // the summary can never disagree with the table beneath it.
  const counts = useMemo(() => severityCounts(alerts), [alerts]);
  const flagged = counts.medium + counts.high + counts.critical;

  // Alerts arrive sorted by risk, so a page of 400 contains every wallet above
  // "low" unless that many are flagged. The low count therefore comes from the
  // scored total rather than from the page, which keeps the chart honest even
  // when the table is showing a slice.
  const lowCount =
    health?.wallets_scored != null
      ? Math.max(health.wallets_scored - flagged, 0)
      : counts.low;
  const pageMayBeTruncated = alerts.length > 0 && counts.low === 0;

  const chartData = useMemo(
    () =>
      SEVERITIES.map((severity) => ({
        severity: SEVERITY_META[severity].label,
        key: severity,
        count: severity === "low" ? lowCount : counts[severity],
      })),
    [counts, lowCount],
  );

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return alerts
      .filter(
        (a) =>
          (severityFilter === "all" || a.severity === severityFilter) &&
          (!needle || a.wallet_address.toLowerCase().includes(needle)),
      )
      .sort(SORTS[sortKey]);
  }, [alerts, severityFilter, sortKey, query]);

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <section className="grid gap-4 lg:grid-cols-[repeat(3,minmax(0,1fr))_1.6fr]">
          <Stat
            label="Transactions processed"
            value={health?.transactions?.toLocaleString() ?? "—"}
          />
          <Stat
            label="Wallets scored"
            value={health?.wallets_scored?.toLocaleString() ?? "—"}
          />
          <Stat
            label="Wallets flagged"
            value={flagged.toLocaleString()}
            hint={`${counts.critical} critical · ${counts.high} high`}
            accent={flagged > 0}
          />
          <div className="rounded-lg border border-edge bg-surface p-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-3">
              Risk distribution
              {pageMayBeTruncated && (
                <span className="ml-2 normal-case tracking-normal text-risk-medium">
                  showing the top {alerts.length}
                </span>
              )}
            </p>
            <div className="mt-3 h-24">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={chartData}
                  margin={{ top: 14, right: 4, bottom: 0, left: -22 }}
                >
                  <XAxis
                    dataKey="severity"
                    tick={{ fill: "#6f7a89", fontSize: 11 }}
                    axisLine={{ stroke: "#2a333f" }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fill: "#6f7a89", fontSize: 11 }}
                    axisLine={false}
                    tickLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip
                    cursor={{ fill: "#1e2530" }}
                    contentStyle={{
                      background: "#171d26",
                      border: "1px solid #2a333f",
                      borderRadius: 8,
                      fontSize: 12,
                      color: "#e7ebf1",
                    }}
                  />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={38}>
                    <LabelList
                      dataKey="count"
                      position="top"
                      fill="#a2acbb"
                      fontSize={11}
                      fontFamily='"IBM Plex Mono", monospace'
                    />
                    {chartData.map((entry) => (
                      <Cell key={entry.key} fill={SEVERITY_META[entry.key].hex} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </section>

        <section className="overflow-hidden rounded-lg border border-edge bg-surface">
          <header className="flex flex-wrap items-center gap-3 border-b border-edge px-4 py-3">
            <h2 className="text-sm font-medium text-ink">Wallet alerts</h2>

            <div className="ml-auto flex flex-wrap items-center gap-2">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter by address…"
                className="w-44 rounded-md border border-edge bg-surface-2 px-2.5 py-1.5 font-mono text-xs text-ink placeholder:text-ink-3 outline-none transition focus:border-accent"
              />
              <select
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
                className="rounded-md border border-edge bg-surface-2 px-2 py-1.5 text-xs text-ink outline-none transition hover:border-edge-strong"
              >
                <option value="all">All severities</option>
                {SEVERITIES.slice()
                  .reverse()
                  .map((s) => (
                    <option key={s} value={s}>
                      {SEVERITY_META[s].label} only
                    </option>
                  ))}
              </select>
            </div>
          </header>

          {error && (
            <p className="px-4 py-6 text-sm text-risk-critical">{error}</p>
          )}

          {loading && !error && (
            <p className="px-4 py-6 text-sm text-ink-3">Loading alerts…</p>
          )}

          {!loading && !error && rows.length === 0 && (
            <p className="px-4 py-6 text-sm text-ink-3">
              {alerts.length === 0
                ? "Nothing scored yet — upload a dataset to begin."
                : "No wallets match this filter."}
            </p>
          )}

          {rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[46rem] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-edge">
                    <Th sortKey="wallet_address" active={sortKey} onSort={setSortKey}>
                      Wallet
                    </Th>
                    <Th sortKey="risk_score" active={sortKey} onSort={setSortKey} right>
                      Risk
                    </Th>
                    <Th sortKey="confidence" active={sortKey} onSort={setSortKey} right>
                      Confidence
                    </Th>
                    <Th sortKey="severity" active={sortKey} onSort={setSortKey}>
                      Severity
                    </Th>
                    <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.12em] font-medium text-ink-3">
                      Top reason
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, 300).map((alert) => {
                    const meta = SEVERITY_META[alert.severity];
                    const isSelected = alert.wallet_address === selected;
                    return (
                      <tr
                        key={alert.wallet_address}
                        onClick={() => onSelect(alert.wallet_address)}
                        className={`cursor-pointer border-b border-edge/60 transition last:border-0 ${
                          isSelected ? "bg-accent-dim" : "hover:bg-surface-2"
                        }`}
                      >
                        <td
                          className="px-4 py-2.5 font-mono text-xs text-ink-2"
                          title={alert.wallet_address}
                        >
                          {truncateAddress(alert.wallet_address)}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <span
                            className="font-mono text-xs tabular-nums"
                            style={{ color: riskColor(alert.risk_score) }}
                          >
                            {formatScore(alert.risk_score)}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums text-ink-2">
                          {formatPercent(alert.confidence)}
                        </td>
                        <td className="px-4 py-2.5">
                          <span
                            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${meta.chip}`}
                          >
                            {meta.label}
                          </span>
                        </td>
                        <td className="max-w-md truncate px-4 py-2.5 text-xs text-ink-3">
                          {alert.top_reasons?.[0] ?? "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {rows.length > 300 && (
                <p className="border-t border-edge px-4 py-2.5 text-xs text-ink-3">
                  Showing the first 300 of {rows.length.toLocaleString()} matching
                  wallets.
                </p>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value, hint, accent }) {
  return (
    <div className="rounded-lg border border-edge bg-surface p-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-3">
        {label}
      </p>
      <p
        className={`mt-2 font-mono text-3xl tabular-nums ${
          accent ? "text-risk-critical" : "text-ink"
        }`}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-ink-3">{hint}</p>}
    </div>
  );
}

function Th({ children, sortKey, active, onSort, right }) {
  const isActive = active === sortKey;
  return (
    <th
      className={`px-4 py-2.5 font-mono text-[10px] font-medium uppercase tracking-[0.12em] ${
        right ? "text-right" : "text-left"
      }`}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`transition ${isActive ? "text-accent" : "text-ink-3 hover:text-ink-2"}`}
      >
        {children}
        {isActive && <span aria-hidden="true"> ↓</span>}
      </button>
    </th>
  );
}
