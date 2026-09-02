/** Shared vocabulary for showing risk, so the graph and the table agree. */

export const SEVERITIES = ["low", "medium", "high", "critical"];

export const SEVERITY_META = {
  low: {
    label: "Low",
    hex: "#3f9e6e",
    chip: "bg-risk-low/15 text-risk-low border-risk-low/30",
  },
  medium: {
    label: "Medium",
    hex: "#d9a441",
    chip: "bg-risk-medium/15 text-risk-medium border-risk-medium/30",
  },
  high: {
    label: "High",
    hex: "#de7a3c",
    chip: "bg-risk-high/15 text-risk-high border-risk-high/30",
  },
  critical: {
    label: "Critical",
    hex: "#de5342",
    chip: "bg-risk-critical/15 text-risk-critical border-risk-critical/30",
  },
};

/** Severities that deserve to catch the eye without being clicked. */
export const URGENT = new Set(["high", "critical"]);

const STOPS = [
  [0.0, [63, 158, 110]], // green
  [0.5, [217, 164, 65]], // amber
  [1.0, [222, 83, 66]], // red
];

/**
 * A wallet's colour on the green-to-red risk ramp.
 *
 * `null` means "not scored yet", which is a different thing from "scored as
 * safe" - it renders grey rather than green, so an unassessed node is never
 * mistaken for a cleared one.
 */
export function riskColor(score) {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return "#6f7a89";
  }
  const value = Math.min(Math.max(score, 0), 1);

  let lower = STOPS[0];
  let upper = STOPS[STOPS.length - 1];
  for (let i = 0; i < STOPS.length - 1; i += 1) {
    if (value >= STOPS[i][0] && value <= STOPS[i + 1][0]) {
      lower = STOPS[i];
      upper = STOPS[i + 1];
      break;
    }
  }

  const span = upper[0] - lower[0] || 1;
  const t = (value - lower[0]) / span;
  const channel = (i) => Math.round(lower[1][i] + (upper[1][i] - lower[1][i]) * t);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

/** Bitcoin addresses are 34 characters; the middle carries no meaning to a reader. */
export function truncateAddress(address, lead = 6, tail = 4) {
  if (!address || address.length <= lead + tail + 1) return address ?? "";
  return `${address.slice(0, lead)}…${address.slice(-tail)}`;
}

export const formatScore = (value) =>
  value === null || value === undefined ? "—" : value.toFixed(3);

export const formatPercent = (value) =>
  value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;

/** Bucket a list of alerts by severity, always returning all four keys. */
export function severityCounts(alerts) {
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
  for (const alert of alerts) {
    if (counts[alert.severity] !== undefined) counts[alert.severity] += 1;
  }
  return counts;
}
