import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { getGraph } from "../lib/api";
import { URGENT, riskColor, truncateAddress } from "../lib/risk";

const RISK_FILTERS = [
  { label: "All wallets", value: 0 },
  { label: "Medium and up", value: 0.3 },
  { label: "High and up", value: 0.55 },
  { label: "Critical only", value: 0.8 },
];

// Medium-and-up opens on a view with structure rather than a hairball: the
// full graph is ~1,700 nodes and 20,000 links, where nothing is legible.
const DEFAULT_MIN_RISK = 0.3;
const NODE_LIMIT = 700;

// The smallest a node's clickable radius may get, in CSS pixels.
//
// This is the whole reason clicking used to fail. The hit area was derived
// from the node's drawn radius in *graph* units, and at the default zoom the
// graph is scaled to about 0.18 - so a wallet ended up with a hit radius of
// roughly one pixel. It was not that the nodes were small and fiddly; the
// target was smaller than the cursor's own hotspot, and on a narrower window
// it went sub-pixel and nothing was clickable at all. Dragging failed for the
// same reason, since it uses the same hit test.
//
// The pointer area is therefore sized in screen space and converted back into
// graph units, so it stays a comfortable target at every zoom level.
const MIN_HIT_RADIUS_PX = 9;

const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export default function GraphView({ selected, onSelect, reloadKey }) {
  const wrapRef = useRef(null);
  const fgRef = useRef(null);

  const [raw, setRaw] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [minRisk, setMinRisk] = useState(DEFAULT_MIN_RISK);
  const [showIps, setShowIps] = useState(true);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hovered, setHovered] = useState(null);
  const [hoverClickable, setHoverClickable] = useState(false);
  const fitted = useRef(false);

  // The canvas painter reads these instead of closing over state.
  //
  // nodeCanvasObject is a prop: giving react-force-graph a new function
  // identity makes it reconfigure. The pulse used to live in state and tick
  // fourteen times a second, so the whole component - toolbar, legend and all -
  // re-rendered at 14 Hz and the graph was reconfigured just as often. That is
  // pure overhead on a fast machine and visible jank on a slow one.
  const selectedRef = useRef(selected);
  const hoveredRef = useRef(hovered);
  const pulseRef = useRef(0);

  // --- data -------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getGraph({ minRisk, limit: NODE_LIMIT, includeIps: showIps })
      .then((data) => {
        if (!cancelled) setRaw(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setRaw(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    // A new filter means a new layout, so let it re-fit once it settles.
    fitted.current = false;

    return () => {
      cancelled = true;
    };
  }, [minRisk, showIps, reloadKey]);

  // react-force-graph mutates what it is given - it writes x/y onto nodes and
  // swaps link endpoints for node objects. Handing it a copy keeps the fetched
  // payload clean, so re-filtering does not rebuild from mutated data.
  const graphData = useMemo(() => {
    if (!raw) return { nodes: [], links: [] };
    return {
      nodes: raw.nodes.map((n) => ({ ...n })),
      links: raw.links.map((l) => ({ ...l })),
    };
  }, [raw]);

  const urgentCount = useMemo(
    () => graphData.nodes.filter((n) => URGENT.has(n.severity)).length,
    [graphData],
  );

  // --- sizing -----------------------------------------------------------
  useEffect(() => {
    const element = wrapRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Selection and hover only need to reach the painter, not trigger a render.
  // The canvas is repainted explicitly instead.
  useEffect(() => {
    selectedRef.current = selected;
    fgRef.current?.refresh?.();
  }, [selected]);

  useEffect(() => {
    hoveredRef.current = hovered;
    fgRef.current?.refresh?.();
  }, [hovered]);

  // --- the pulse --------------------------------------------------------
  // The simulation stops repainting once it cools, so a pulse drawn from the
  // clock needs the canvas nudged. Driven by requestAnimationFrame rather than
  // setInterval: it writes to a ref instead of state, so nothing re-renders,
  // and the browser suspends it while the tab is hidden. Only runs while
  // urgent nodes are on screen, and not at all under reduced motion.
  useEffect(() => {
    if (!urgentCount || prefersReducedMotion()) return undefined;
    let frame;
    let last = 0;
    const tick = (time) => {
      if (time - last > 80) {
        last = time;
        pulseRef.current = time / 1000;
        fgRef.current?.refresh?.();
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [urgentCount]);

  // --- drawing ----------------------------------------------------------
  const drawNode = useCallback(
    (node, ctx, globalScale) => {
      const isIp = node.type === "ip";
      const risk = node.risk_score;
      const isSelected = node.id === selectedRef.current;
      const isHovered = node.id === hoveredRef.current;

      // Wallets grow with risk so the eye lands on them first; IP nodes stay a
      // fixed size because they carry no score of their own.
      const radius = isIp ? 2.6 : 3 + (risk ?? 0) * 3.4;
      const color = isIp ? "#5c7089" : riskColor(risk);

      if (URGENT.has(node.severity)) {
        const phase = prefersReducedMotion()
          ? 0.5
          : (Math.sin(pulseRef.current * 2.2) + 1) / 2;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 2.5 + phase * 3, 0, 2 * Math.PI);
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.15 + phase * 0.4;
        ctx.lineWidth = 1.2;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      ctx.beginPath();
      if (isIp) {
        ctx.rect(node.x - radius, node.y - radius, radius * 2, radius * 2);
      } else {
        ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
      }
      ctx.fillStyle = color;
      ctx.fill();

      if (isSelected || isHovered) {
        ctx.strokeStyle = isSelected ? "#e7ebf1" : "#4e92d4";
        ctx.lineWidth = isSelected ? 1.6 : 1.2;
        ctx.stroke();
      }

      // Labels only once zoomed in far enough for them not to overlap.
      if (globalScale > 3.2 || isSelected || isHovered) {
        ctx.font = `${Math.max(2.5, 9 / globalScale)}px "IBM Plex Mono", monospace`;
        ctx.fillStyle = "#a2acbb";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(
          isIp ? node.id : truncateAddress(node.id, 5, 3),
          node.x,
          node.y + radius + 1.5,
        );
      }
    },
    // No dependencies on purpose: a stable painter means the graph is never
    // reconfigured mid-interaction. Everything it needs comes from refs.
    [],
  );

  const paintPointerArea = useCallback((node, color, ctx, globalScale) => {
    const isIp = node.type === "ip";
    const drawn = isIp ? 2.6 : 3 + (node.risk_score ?? 0) * 3.4;
    // globalScale converts graph units to screen pixels, so dividing by it
    // turns a pixel floor back into the graph units this canvas is drawn in.
    // Without it the target shrinks with the zoom until it is unclickable.
    //
    // IP nodes get a smaller floor deliberately. They are draggable but not
    // clickable, and giving them the same generous target would let them
    // intercept clicks meant for the wallets sitting next to them.
    const floorPx = isIp ? MIN_HIT_RADIUS_PX * 0.5 : MIN_HIT_RADIUS_PX;
    const radius = Math.max(drawn + 2, floorPx / (globalScale || 1));

    ctx.fillStyle = color;
    ctx.beginPath();
    if (isIp) {
      ctx.rect(node.x - radius, node.y - radius, radius * 2, radius * 2);
    } else {
      ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
    }
    ctx.fill();
  }, []);

  const linkColor = useCallback(
    (link) => (link.kind === "broadcast" ? "#26303c" : "#33404f"),
    [],
  );

  return (
    <div className="relative flex h-full flex-col">
      <Toolbar
        minRisk={minRisk}
        onMinRisk={setMinRisk}
        showIps={showIps}
        onShowIps={setShowIps}
        payload={raw}
        urgentCount={urgentCount}
      />

      <div
        ref={wrapRef}
        className={`relative flex-1 overflow-hidden bg-ground ${
          hoverClickable ? "cursor-pointer" : "cursor-grab"
        }`}
      >
        {loading && <Overlay><Spinner /> <span>Loading graph…</span></Overlay>}

        {error && (
          <Overlay>
            <div className="max-w-sm rounded-lg border border-risk-critical/40 bg-risk-critical/10 px-4 py-3 text-center">
              <p className="text-sm text-risk-critical">{error}</p>
              <p className="mt-2 text-xs text-ink-3">
                Start it with{" "}
                <code className="font-mono">uvicorn app.main:app --port 8000</code>,
                then upload a dataset.
              </p>
            </div>
          </Overlay>
        )}

        {!loading && !error && graphData.nodes.length === 0 && (
          <Overlay>
            <p className="text-sm text-ink-2">
              No wallets at this risk level. Try widening the filter.
            </p>
          </Overlay>
        )}

        {size.width > 0 && (
          <ForceGraph2D
            ref={fgRef}
            width={size.width}
            height={size.height}
            graphData={graphData}
            backgroundColor="#0f1319"
            nodeCanvasObject={drawNode}
            nodePointerAreaPaint={paintPointerArea}
            nodeLabel={(n) =>
              n.type === "ip"
                ? `IP ${n.id}`
                : `${n.id}\nrisk ${(n.risk_score ?? 0).toFixed(3)} · ${n.severity ?? "unscored"}`
            }
            linkColor={linkColor}
            linkWidth={(l) => (l.kind === "broadcast" ? 0.3 : 0.6)}
            linkDirectionalParticles={0}
            onNodeClick={(node) => node.type !== "ip" && onSelect(node.id)}
            onNodeHover={(node) => {
              setHovered(node?.id ?? null);
              // Only wallets open the panel, so only wallets get a pointer.
              setHoverClickable(Boolean(node) && node.type !== "ip");
            }}
            cooldownTicks={120}
            d3VelocityDecay={0.32}
            onEngineStop={() => {
              // Only on the first settle after a load: re-fitting on every
              // stop would yank the view out from under someone who has
              // panned or zoomed to look at something.
              if (fitted.current) return;
              fitted.current = true;
              fgRef.current?.zoomToFit(500, 70);
            }}
          />
        )}

        <Legend />
      </div>
    </div>
  );
}

function Toolbar({ minRisk, onMinRisk, showIps, onShowIps, payload, urgentCount }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-edge bg-surface px-5 py-3">
      <label className="flex items-center gap-2 text-xs text-ink-2">
        <span className="font-mono uppercase tracking-[0.12em] text-ink-3">
          Show
        </span>
        <select
          value={minRisk}
          onChange={(e) => onMinRisk(Number(e.target.value))}
          className="rounded-md border border-edge bg-surface-2 px-2 py-1 text-xs text-ink outline-none transition hover:border-edge-strong"
        >
          {RISK_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-2">
        <input
          type="checkbox"
          checked={showIps}
          onChange={(e) => onShowIps(e.target.checked)}
          className="accent-accent"
        />
        Show IP nodes
      </label>

      {payload && (
        <p className="ml-auto font-mono text-[11px] text-ink-3">
          {payload.nodes.length.toLocaleString()} of{" "}
          {payload.total_nodes.toLocaleString()} nodes ·{" "}
          {payload.links.length.toLocaleString()} links
          {urgentCount > 0 && (
            <span className="ml-2 text-risk-critical">
              {urgentCount} high risk
            </span>
          )}
        </p>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="pointer-events-none absolute bottom-4 left-4 rounded-lg border border-edge bg-surface/90 px-3 py-3 backdrop-blur">
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-3">
        Risk
      </p>
      <div className="mt-2 flex items-center gap-2">
        <div
          className="h-2 w-28 rounded-full"
          style={{
            background:
              "linear-gradient(90deg, #3f9e6e 0%, #d9a441 50%, #de5342 100%)",
          }}
        />
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-ink-3">
        <span>0.0</span>
        <span>1.0</span>
      </div>
      <div className="mt-3 flex flex-col gap-1.5 text-[11px] text-ink-2">
        <span className="flex items-center gap-2">
          <span className="inline-block size-2.5 rounded-full bg-ink-2" />
          Wallet
        </span>
        <span className="flex items-center gap-2">
          <span className="inline-block size-2.5 bg-[#5c7089]" />
          IP address
        </span>
        <span className="flex items-center gap-2">
          <span className="inline-block size-2.5 rounded-full ring-2 ring-risk-critical" />
          High or critical
        </span>
      </div>
    </div>
  );
}

const Overlay = ({ children }) => (
  <div className="absolute inset-0 z-10 flex items-center justify-center gap-3 bg-ground/80 text-sm text-ink-2">
    {children}
  </div>
);

const Spinner = () => (
  <span className="inline-block size-4 animate-spin rounded-full border-2 border-edge-strong border-t-accent" />
);
