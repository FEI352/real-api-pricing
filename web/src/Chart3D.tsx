import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Group, Row, SiteData, State } from "./types";
import type { ChartHandle } from "./Chart";
import {
  color,
  costPerTaskX,
  displayPlan,
  isLocalHardware,
  manufacturer,
  mappingSpeed,
  mappingTime,
  metricLabel,
  number,
  price,
  searchHits,
} from "./domain";
import { providerLogoUrl } from "./ProviderLogo";
import { formatTickPrice, formatTickTime } from "./chartScene";

export type EngineMode = "canopy" | "triad" | "three" | "svg4d";

interface Node3D {
  key: string;
  family: string;
  modelDisplay: string;
  planDisplay: string;
  channel: string;
  vendor: string;
  maker: string;
  hexColor: string;
  cost: number;
  time: number;
  score: number;
  tps: number;
  tokens: number;
  isFrontier3D: boolean;
  isCostFrontier: boolean;
  isTimeFrontier: boolean;
  isDualWinner: boolean;
  rows: Row[];
}

declare global {
  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    THREE?: any;
  }
}

const scriptPromises = new Map<string, Promise<void>>();
function loadVendorScript(src: string): Promise<void> {
  const existing = scriptPromises.get(src);
  if (existing) return existing;
  const p = new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(s);
  });
  scriptPromises.set(src, p);
  return p;
}

const FAMILY_NOISE =
  /\s*\((?:v\d+|max|xhigh|high|medium|low)\)\s*$|\s+Contributor\s*$|\s+Eco\s*$/i;

function cleanFamily(name: string): string {
  return name.replace(FAMILY_NOISE, "").trim();
}

function normalizeEngineParam(eng: string | null): EngineMode | null {
  if (eng === "canopy" || eng === "echarts") return "canopy";
  if (eng === "triad" || eng === "plotly") return "triad";
  if (eng === "three" || eng === "svg4d") return eng;
  return null;
}

function initialEngineFromHash(): EngineMode {
  if (typeof window === "undefined") return "three";
  const raw = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(raw);
  return normalizeEngineParam(params.get("eng")) ?? "three";
}

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180.0;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function describeArc(
  cx: number,
  cy: number,
  r: number,
  startAngle: number,
  endAngle: number,
): string {
  const start = polarToCartesian(cx, cy, r, endAngle);
  const end = polarToCartesian(cx, cy, r, startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${r.toFixed(2)} ${r.toFixed(2)} 0 ${largeArcFlag} 0 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
}

export default function Chart3D({
  rows,
  state,
  data,
  theme,
  highlight,
  onSelect,
  handle,
  toolsSlot,
  selectedCount,
  totalCount,
  isCustomSelection,
  onOpenModelPicker,
}: {
  rows: Row[];
  state: State;
  data: SiteData;
  theme: "light" | "dark";
  highlight: string | null;
  onSelect: (rows: Row[]) => void;
  onSearch?: (query: string, lock: State["lock"]) => void;
  handle: React.RefObject<ChartHandle | null>;
  toolsSlot?: HTMLElement | null;
  selectedCount?: number;
  totalCount?: number;
  isCustomSelection?: boolean;
  onOpenModelPicker?: () => void;
}) {
  const zh = state.lang === "zh";
  const dark = theme === "dark";
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const [width, setWidth] = useState(1180);
  const [engine, setEngine] = useState<EngineMode>(initialEngineFromHash);
  const [dedupeByModel, setDedupeByModel] = useState(true);
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  // Sync URL hash `&eng=...` changes
  useEffect(() => {
    const onHash = () => {
      const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      const mapped = normalizeEngineParam(params.get("eng"));
      if (mapped) setEngine(mapped);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 100) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const height =
    width < 640
      ? engine === "triad"
        ? 640
        : 520
      : engine === "triad"
        ? Math.min(790, Math.max(650, Math.round(width * 0.59)))
        : Math.min(760, Math.max(600, Math.round(width * 0.56)));

  const { nodes, frontier3D, dualWinners, costFrontier, timeFrontier } = useMemo(() => {
    const rawList: Node3D[] = [];
    const byKey = new Map<string, Node3D>();

    for (const r of rows) {
      if (r.score === null || !Number.isFinite(r.score)) continue;
      if (isLocalHardware(r.point)) continue;
      const cost = costPerTaskX(r.point, r.mapping, data);
      const time = mappingTime(r.mapping, data);
      if (cost === null || cost <= 0 || time === null || time <= 0) continue;
      const tps = mappingSpeed(r.mapping, data) ?? 100;
      const aaCost = r.mapping?.mean_cost_usd_per_task;
      const listPrice =
        r.mapping?.benchmark_list_price ?? r.point.list_blended_usd_per_mtok;
      const tokens =
        aaCost && listPrice && listPrice > 0
          ? (aaCost / listPrice) * 1e6
          : time * tps;

      const fam = cleanFamily(r.point.model_display || r.point.model);
      const groupKey = dedupeByModel
        ? fam
        : `${cost.toFixed(6)}|${time.toFixed(2)}|${r.score.toFixed(4)}`;

      const existing = byKey.get(groupKey);
      if (existing) {
        existing.rows.push(r);
        if (cost < existing.cost) {
          existing.cost = cost;
          existing.time = time;
          existing.score = r.score;
          existing.tps = tps;
          existing.tokens = tokens;
          existing.planDisplay = displayPlan(r.point.plan, state.lang);
          existing.channel = r.point.channel;
          existing.hexColor = color(r.point);
        }
      } else {
        const node: Node3D = {
          key: groupKey,
          family: fam,
          modelDisplay: r.point.model_display,
          planDisplay: displayPlan(r.point.plan, state.lang),
          channel: r.point.channel,
          vendor: r.point.vendor,
          maker: manufacturer(r.point.vendor),
          hexColor: color(r.point),
          cost,
          time,
          score: r.score,
          tps,
          tokens,
          isFrontier3D: false,
          isCostFrontier: false,
          isTimeFrontier: false,
          isDualWinner: false,
          rows: [r],
        };
        byKey.set(groupKey, node);
        rawList.push(node);
      }
    }

    // 1. 3D non-dominated set
    for (const a of rawList) {
      a.isFrontier3D = !rawList.some(
        (b) =>
          b !== a &&
          b.cost <= a.cost &&
          b.time <= a.time &&
          b.score >= a.score &&
          (b.cost < a.cost || b.time < a.time || b.score > a.score),
      );
    }

    // 2. 2D Cost Pareto frontier
    const byCost = [...rawList].sort((a, b) => a.cost - b.cost || b.score - a.score);
    let bestC = -Infinity;
    const costF: Node3D[] = [];
    for (const n of byCost) {
      if (n.score > bestC) {
        n.isCostFrontier = true;
        costF.push(n);
        bestC = n.score;
      }
    }

    // 3. 2D Time Pareto frontier
    const byTime = [...rawList].sort((a, b) => a.time - b.time || b.score - a.score);
    let bestT = -Infinity;
    const timeF: Node3D[] = [];
    for (const n of byTime) {
      if (n.score > bestT) {
        n.isTimeFrontier = true;
        timeF.push(n);
        bestT = n.score;
      }
    }

    for (const n of rawList) {
      n.isDualWinner = n.isCostFrontier && n.isTimeFrontier;
    }

    return {
      nodes: rawList.sort((a, b) => b.score - a.score),
      frontier3D: rawList
        .filter((n) => n.isFrontier3D)
        .sort((a, b) => a.cost - b.cost),
      dualWinners: rawList
        .filter((n) => n.isDualWinner)
        .sort((a, b) => b.score - a.score),
      costFrontier: costF,
      timeFrontier: timeF,
    };
  }, [rows, data, dedupeByModel, state.lang]);

  const hitSet = useMemo(() => {
    if (!state.find.trim() && !state.lock) return null;
    const gs: Group[] = nodes.map((n) => ({
      key: n.key,
      price: n.cost,
      plotPrice: n.cost,
      score: n.score,
      rows: n.rows,
    }));
    return searchHits(gs, state.find, state.lock, state.lang).keys;
  }, [nodes, state.find, state.lock, state.lang]);

  useEffect(() => {
    handle.current = {
      async download(_format: "png" | "svg") {
        const c = wrapRef.current?.querySelector("canvas");
        if (!c) return;
        const a = document.createElement("a");
        a.href = c.toDataURL("image/png");
        a.download = `real-plan-${engine}-${state.board}.png`;
        a.click();
      },
    };
    return () => {
      handle.current = null;
    };
  }, [handle, engine, state.board]);

  const hovered = useMemo(
    () => (hoverKey ? nodes.find((n) => n.key === hoverKey) ?? null : null),
    [hoverKey, nodes],
  );
  const board = data.boards[state.board];

  return (
    <div className="chart-wrap" ref={wrapRef}>
      {toolsSlot ? createPortal(<div />, toolsSlot) : null}

      {/* Top Switcher Bar */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "8px 14px",
          padding: "10px 14px",
          borderBottom: "1px solid var(--border, rgba(148,163,184,0.18))",
          fontSize: 12.5,
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          <span className="fee-label">{zh ? "多维引擎方案" : "4D Engine"}</span>
          <div className="segmented">
            <button
              type="button"
              aria-pressed={engine === "canopy"}
              onClick={() => setEngine("canopy")}
            >
              {zh ? "① Three.js 玻璃天幕 3D" : "① Three.js Glass Canopy"}
            </button>
            <button
              type="button"
              aria-pressed={engine === "triad"}
              onClick={() => setEngine("triad")}
            >
              {zh ? "② 原生 SVG 三视图矩阵" : "② SVG 4D Triad"}
            </button>
            <button
              type="button"
              aria-pressed={engine === "three"}
              onClick={() => setEngine("three")}
            >
              {zh ? "③ Three.js 全息徽章舱" : "③ Three.js Logo Deck"}
            </button>
            <button
              type="button"
              aria-pressed={engine === "svg4d"}
              onClick={() => setEngine("svg4d")}
            >
              {zh ? "④ 原生 SVG 四维双图联动" : "④ Native SVG 4D Split"}
            </button>
          </div>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          {onOpenModelPicker && (
            <button
              type="button"
              className="select-models"
              onClick={onOpenModelPicker}
              style={{ fontSize: 12, padding: "4px 10px" }}
              title={
                zh
                  ? "独立于二维图的 3D 模型与套餐选择器（默认排除低于 Qwen3.8 27B 最高 AA 指数的模型）"
                  : "Independent 3D model & plan selector (excludes models below Qwen3.8 27B max AA by default)"
              }
            >
              <span>{zh ? "3D 模型" : "3D Models"}</span>
              {selectedCount !== undefined && totalCount !== undefined && (
                <span className="count">
                  {selectedCount} / {totalCount}
                </span>
              )}
              {isCustomSelection && (
                <span style={{ color: dark ? "#F5B301" : "#B45309", fontWeight: 700 }}>•</span>
              )}
            </button>
          )}
          <span className="fee-label">{zh ? "粒度" : "Scope"}</span>
          <div className="segmented">
            <button
              type="button"
              aria-pressed={dedupeByModel}
              onClick={() => setDedupeByModel(true)}
            >
              {zh ? "每模型最优渠道" : "Best per model"}
            </button>
            <button
              type="button"
              aria-pressed={!dedupeByModel}
              onClick={() => setDedupeByModel(false)}
            >
              {zh ? "全部套餐点" : "All plans"}
            </button>
          </div>
        </div>
      </div>

      {/* Sub-banner showing active frontier */}
      <div
        style={{
          padding: "7px 14px",
          fontSize: 12,
          borderBottom: "1px solid var(--border, rgba(148,163,184,0.12))",
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div>
          <b style={{ color: dark ? "#F5B301" : "#B45309" }}>
            {engine === "svg4d"
              ? zh
                ? `★ 双前沿六边形冠军（${dualWinners.length} 个同时称霸成本与耗时前沿）：`
                : `★ Dual-Frontier Champions (${dualWinners.length} on both Cost & Time frontiers): `
              : zh
                ? `★ 三维帕累托非支配前沿（${frontier3D.length} 个）：`
                : `★ 3D Non-Dominated Frontier (${frontier3D.length}): `}
          </b>
          <span style={{ color: "var(--ink, #F4F7FC)", fontWeight: 600 }}>
            {(engine === "svg4d" ? dualWinners : frontier3D)
              .map((n) => n.family)
              .join("  ·  ")}
          </span>
        </div>
        {hovered && (
          <div style={{ color: "var(--text, #9AA5B8)" }}>
            <b style={{ color: "var(--ink, #F4F7FC)" }}>{hovered.family}</b> ({hovered.planDisplay}) ·{" "}
            {zh ? "成本" : "Cost"} <b>{price(hovered.cost)}</b> ·{" "}
            {zh ? "用时" : "Time"} <b>{formatTickTime(hovered.time)}</b> ·{" "}
            {zh ? "吞吐" : "Speed"} <b>{Math.round(hovered.tps)} tok/s</b> ·{" "}
            Token <b>{Math.round(hovered.tokens / 1000)}k</b> ·{" "}
            {metricLabel(board.metric, state.lang)} <b>{number(hovered.score, state.lang)}</b>
          </div>
        )}
      </div>

      {/* Active Engine Viewport */}
      {engine === "canopy" && (
        <ThreeLogoDeckView
          nodes={nodes}
          costFrontier={costFrontier}
          timeFrontier={timeFrontier}
          width={width}
          height={height}
          dark={dark}
          zh={zh}
          highlight={highlight}
          hitSet={hitSet}
          onSelect={onSelect}
          hoverKey={hoverKey}
          onHover={setHoverKey}
          showCanopy
        />
      )}
      {engine === "triad" && (
        <OrthogonalTriadView
          nodes={nodes}
          costFrontier={costFrontier}
          timeFrontier={timeFrontier}
          dualWinners={dualWinners}
          width={width}
          height={height}
          dark={dark}
          zh={zh}
          highlight={highlight}
          hitSet={hitSet}
          hoverKey={hoverKey}
          onHover={setHoverKey}
          onSelect={onSelect}
        />
      )}
      {engine === "three" && (
        <ThreeLogoDeckView
          nodes={nodes}
          costFrontier={costFrontier}
          timeFrontier={timeFrontier}
          width={width}
          height={height}
          dark={dark}
          zh={zh}
          highlight={highlight}
          hitSet={hitSet}
          onSelect={onSelect}
          hoverKey={hoverKey}
          onHover={setHoverKey}
        />
      )}
      {engine === "svg4d" && (
        <NativeSvg4DSplitView
          nodes={nodes}
          costFrontier={costFrontier}
          timeFrontier={timeFrontier}
          dualWinners={dualWinners}
          width={width}
          height={height}
          dark={dark}
          zh={zh}
          highlight={highlight}
          hitSet={hitSet}
          hoverKey={hoverKey}
          onHover={setHoverKey}
          onSelect={onSelect}
        />
      )}
    </div>
  );
}

/* ============================================================================
 * ENGINE 2: Native SVG 4D Orthogonal Triad (Cost×Score | Time×Score | Cost×Time)
 * ========================================================================== */
function OrthogonalTriadView({
  nodes,
  costFrontier,
  timeFrontier,
  dualWinners,
  width,
  height,
  dark,
  zh,
  highlight,
  hitSet,
  hoverKey,
  onHover,
  onSelect,
}: {
  nodes: Node3D[];
  costFrontier: Node3D[];
  timeFrontier: Node3D[];
  dualWinners: Node3D[];
  width: number;
  height: number;
  dark: boolean;
  zh: boolean;
  highlight: string | null;
  hitSet: Set<string> | null;
  hoverKey: string | null;
  onHover: (key: string | null) => void;
  onSelect: (rows: Row[]) => void;
}) {
  const padL = 52;
  const padR = 20;
  const padT = 36;
  const padB = 34;
  const gapX = 46;
  const gapY = 48;

  const innerW = Math.max(460, width - padL - padR - gapX);
  const leftW = Math.round(innerW * 0.52);
  const rightW = innerW - leftW;
  const innerH = Math.max(400, height - padT - padB - gapY);
  const topH = Math.round(innerH * 0.53);
  const botH = innerH - topH;

  const xL0 = padL;
  const xL1 = padL + leftW;
  const xR0 = xL1 + gapX;
  const xR1 = xR0 + rightW;
  const yT0 = padT;
  const yT1 = padT + topH;
  const yB0 = yT1 + gapY;
  const yB1 = yB0 + botH;

  const xs = nodes.map((n) => Math.log10(n.cost));
  const ys = nodes.map((n) => Math.log10(n.time));
  const zs = nodes.map((n) => n.score);

  const cMin = Math.min(...xs, -2.5) - 0.18;
  const cMax = Math.max(...xs, 0.8) + 0.18;
  const tMin = Math.min(...ys, 2.0) - 0.14;
  const tMax = Math.max(...ys, 3.55) + 0.22;
  const sMin = Math.max(0, Math.min(...zs, 8) - 6);
  const sMax = Math.max(...zs, 64) + 9;

  const tokMin = Math.min(...nodes.map((n) => Math.sqrt(n.tokens)), 100);
  const tokMax = Math.max(...nodes.map((n) => Math.sqrt(n.tokens)), 600);
  const tpsMin = Math.min(...nodes.map((n) => n.tps), 30);
  const tpsMax = Math.max(...nodes.map((n) => n.tps), 900);

  // Panel 1 & 3 share Cost X (cheaper -> right)
  const mapCostX = (cost: number) =>
    xL0 + ((cMax - Math.log10(cost)) / Math.max(1e-6, cMax - cMin)) * leftW;
  // Panel 1 & 2 share Score Y (higher -> top)
  const mapScoreY = (score: number) =>
    yT0 + ((sMax - score) / Math.max(1e-6, sMax - sMin)) * topH;
  // Panel 2 Time X (faster -> right)
  const mapTimeX = (time: number) =>
    xR0 + ((tMax - Math.log10(time)) / Math.max(1e-6, tMax - tMin)) * rightW;
  // Panel 3 Time Y (faster -> top, so top-right of Panel 3 is Fast & Cheap Sweet Spot)
  const mapPlanTimeY = (time: number) =>
    yB0 + ((Math.log10(time) - tMin) / Math.max(1e-6, tMax - tMin)) * botH;

  // 2D Cost x Time Pareto frontier (cheaper AND faster)
  const costTimeFrontier = useMemo(() => {
    const byCost = [...nodes].sort((a, b) => a.cost - b.cost || a.time - b.time);
    let bestT = Infinity;
    const out: Node3D[] = [];
    for (const n of byCost) {
      if (n.time < bestT) {
        out.push(n);
        bestT = n.time;
      }
    }
    return out;
  }, [nodes]);
  const ctKeys = useMemo(() => new Set(costTimeFrontier.map((n) => n.key)), [costTimeFrontier]);

  const bg = dark ? "#0B0E14" : "#F8FAFC";
  const panelBg = dark ? "#101622" : "#FFFFFF";
  const panelAlt = dark ? "#0E141F" : "#FAFBFD";
  const grid = dark ? "#1C2433" : "#E6EBF2";
  const border = dark ? "#2C384E" : "#CBD5E1";
  const ink = dark ? "#F4F7FC" : "#0F172A";
  const text = dark ? "#9AA5B8" : "#475569";
  const gold = dark ? "#FBBF24" : "#D97706";
  const goldText = dark ? "#FEF08A" : "#78350F";
  const goldBg = dark ? "#3B2808" : "#FEF3C7";
  const cyan = dark ? "#38BDF8" : "#0284C7";
  const emerald = dark ? "#34D399" : "#059669";

  const costTicks = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5].filter(
    (v) => Math.log10(v) >= cMin + 0.04 && Math.log10(v) <= cMax - 0.04,
  );
  const timeTicks = [120, 300, 600, 1200, 1800, 3000].filter(
    (v) => Math.log10(v) >= tMin + 0.03 && Math.log10(v) <= tMax - 0.03,
  );
  const scoreTicks = [10, 20, 30, 40, 50, 60].filter((v) => v >= sMin + 2 && v <= sMax - 2);

  // Greedy 2D collision-free label placement per panel
  const placePanelLabels = (
    items: {
      node: Node3D;
      x: number;
      y: number;
      priority: number;
      force: boolean;
      isStar: boolean;
    }[],
    bx0: number,
    by0: number,
    bx1: number,
    by1: number,
  ) => {
    const placed: [number, number, number, number][] = [];
    // Reserve every visible node core so labels/pills avoid sitting directly on sibling dots
    for (const item of items) {
      const r = item.isStar ? 10 : 7;
      placed.push([item.x - r, item.y - r, item.x + r, item.y + r]);
    }
    const results = new Map<string, { lx: number; ly: number; anchor: "middle" | "start" | "end"; ox: number; oy: number }>();
    const ordered = [...items].sort((a, b) => b.priority - a.priority);
    const offsets: [number, number, "middle" | "start" | "end"][] = [
      [0, -16, "middle"],
      [0, 22, "middle"],
      [15, 3, "start"],
      [-15, 3, "end"],
      [14, -14, "start"],
      [-14, -14, "end"],
      [14, 19, "start"],
      [-14, 19, "end"],
      [22, -26, "start"],
      [-22, -26, "end"],
      [22, 26, "start"],
      [-22, 26, "end"],
      [0, -34, "middle"],
      [0, 38, "middle"],
      [38, -14, "start"],
      [-38, -14, "end"],
      [38, 19, "start"],
      [-38, 19, "end"],
      [48, -32, "start"],
      [-48, -32, "end"],
      [48, 34, "start"],
      [-48, 34, "end"],
      [0, -52, "middle"],
      [0, 54, "middle"],
      [68, -18, "start"],
      [-68, -18, "end"],
      [68, 22, "start"],
      [-68, 22, "end"],
      [0, 72, "middle"],
      [0, -70, "middle"],
      [96, -24, "start"],
      [-96, -24, "end"],
      [96, 28, "start"],
      [-96, 28, "end"],
    ];
    for (const item of ordered) {
      if (!item.force && nodes.length > 34) continue;
      const labelStr = item.isStar ? `★ ${item.node.family}` : item.node.family;
      const w = labelStr.length * (item.isStar ? 6.8 : 6.6) + (item.isStar ? 22 : 14);
      const h = item.isStar ? 20 : 15;
      let chosen: { lx: number; ly: number; anchor: "middle" | "start" | "end"; ox: number; oy: number } | null = null;
      for (const [dx, dy, anchor] of offsets) {
        const lx = item.x + dx;
        const ly = item.y + dy;
        const x0 = anchor === "middle" ? lx - w / 2 : anchor === "start" ? lx : lx - w;
        const x1 = x0 + w;
        const y0 = ly - h + 3;
        const y1 = ly + 4;
        if (x0 < bx0 + 3 || x1 > bx1 - 3 || y0 < by0 + 3 || y1 > by1 - 3) continue;
        const hit = placed.some((b) => !(x1 < b[0] || x0 > b[2] || y1 < b[1] || y0 > b[3]));
        if (!hit) {
          placed.push([x0 - 1, y0 - 1, x1 + 1, y1 + 1]);
          chosen = { lx, ly, anchor, ox: item.x, oy: item.y };
          break;
        }
      }
      if (!chosen && item.force) {
        let bestOverlap = Infinity;
        let bestCand: { lx: number; ly: number; anchor: "middle" | "start" | "end"; box: [number, number, number, number] } | null = null;
        for (const [dx, dy, anchor] of offsets) {
          const rawLx = item.x + dx;
          const rawLy = item.y + dy;
          const x0Raw = anchor === "middle" ? rawLx - w / 2 : anchor === "start" ? rawLx : rawLx - w;
          const x0 = Math.max(bx0 + 4, Math.min(bx1 - w - 4, x0Raw));
          const x1 = x0 + w;
          const lx = anchor === "middle" ? x0 + w / 2 : anchor === "start" ? x0 : x1;
          const ly = Math.max(by0 + h, Math.min(by1 - 6, rawLy));
          const y0 = ly - h + 3;
          const y1 = ly + 4;
          let area = 0;
          for (const b of placed) {
            const ox = Math.max(0, Math.min(x1, b[2]) - Math.max(x0, b[0]));
            const oy = Math.max(0, Math.min(y1, b[3]) - Math.max(y0, b[1]));
            area += ox * oy;
          }
          if (area < bestOverlap) {
            bestOverlap = area;
            bestCand = { lx, ly, anchor, box: [x0 - 1, y0 - 1, x1 + 1, y1 + 1] };
            if (area === 0) break;
          }
        }
        if (bestCand) {
          placed.push(bestCand.box);
          chosen = { lx: bestCand.lx, ly: bestCand.ly, anchor: bestCand.anchor, ox: item.x, oy: item.y };
        }
      }
      if (chosen) results.set(item.node.key, chosen);
    }
    return results;
  };

  const isMuted = (n: Node3D) =>
    (highlight !== null && n.channel !== highlight) ||
    (hitSet !== null && !hitSet.has(n.key));

  const labelsP1 = placePanelLabels(
    nodes
      .filter((n) => !isMuted(n))
      .map((n) => ({
        node: n,
        x: mapCostX(n.cost),
        y: mapScoreY(n.score),
        priority: (n.isFrontier3D ? 1000 : 0) + (hoverKey === n.key ? 2000 : 0) + n.score,
        force: n.isFrontier3D || n.isCostFrontier || hoverKey === n.key || Boolean(hitSet?.has(n.key)),
        isStar: n.isFrontier3D,
      })),
    xL0,
    yT0,
    xL1,
    yT1,
  );
  const labelsP2 = placePanelLabels(
    nodes
      .filter((n) => !isMuted(n))
      .map((n) => ({
        node: n,
        x: mapTimeX(n.time),
        y: mapScoreY(n.score),
        priority: (n.isFrontier3D ? 1000 : 0) + (hoverKey === n.key ? 2000 : 0) + n.score,
        force: n.isFrontier3D || n.isTimeFrontier || hoverKey === n.key || Boolean(hitSet?.has(n.key)),
        isStar: n.isFrontier3D,
      })),
    xR0,
    yT0,
    xR1,
    yT1,
  );
  const labelsP3 = placePanelLabels(
    nodes
      .filter((n) => !isMuted(n))
      .map((n) => ({
        node: n,
        x: mapCostX(n.cost),
        y: mapPlanTimeY(n.time),
        priority: (ctKeys.has(n.key) ? 1200 : 0) + (n.isFrontier3D ? 900 : 0) + (hoverKey === n.key ? 2000 : 0) + n.score,
        force: ctKeys.has(n.key) || n.isFrontier3D || hoverKey === n.key || Boolean(hitSet?.has(n.key)),
        isStar: n.isFrontier3D,
      })),
    xL0,
    yB0,
    xL1,
    yB1,
  );

  const activeNode =
    (hoverKey ? nodes.find((n) => n.key === hoverKey) ?? null : null) ??
    dualWinners[0] ??
    nodes[0] ??
    null;

  // Normalized rank bars [0..1] for telemetry inspector
  const normCost = activeNode
    ? (cMax - Math.log10(activeNode.cost)) / Math.max(1e-6, cMax - cMin)
    : 0;
  const normSpeed = activeNode
    ? (tMax - Math.log10(activeNode.time)) / Math.max(1e-6, tMax - tMin)
    : 0;
  const normScore = activeNode
    ? (activeNode.score - sMin) / Math.max(1e-6, sMax - sMin)
    : 0;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block", userSelect: "none", background: bg }}
    >
      <defs>
        <clipPath id="triad-p1"><rect x={xL0} y={yT0} width={leftW} height={topH} rx={6} /></clipPath>
        <clipPath id="triad-p2"><rect x={xR0} y={yT0} width={rightW} height={topH} rx={6} /></clipPath>
        <clipPath id="triad-p3"><rect x={xL0} y={yB0} width={leftW} height={botH} rx={6} /></clipPath>
        <linearGradient id="triad-sweet" x1="1" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={emerald} stopOpacity={dark ? 0.18 : 0.14} />
          <stop offset="65%" stopColor={emerald} stopOpacity={0.02} />
          <stop offset="100%" stopColor={emerald} stopOpacity={0} />
        </linearGradient>
      </defs>

      {/* Panel Backgrounds */}
      <rect x={xL0} y={yT0} width={leftW} height={topH} fill={panelBg} stroke={border} rx={6} />
      <rect x={xR0} y={yT0} width={rightW} height={topH} fill={panelBg} stroke={border} rx={6} />
      <rect x={xL0} y={yB0} width={leftW} height={botH} fill={panelBg} stroke={border} rx={6} />
      <rect x={xR0} y={yB0} width={rightW} height={botH} fill={panelAlt} stroke={border} rx={8} />

      {/* Sweet Spot Gradient in Plan View (top-right = Fast & Cheap) */}
      <rect x={xL0} y={yB0} width={leftW} height={botH} fill="url(#triad-sweet)" clipPath="url(#triad-p3)" />
      <text
        x={xL1 - 10}
        y={yB0 + 16}
        fontSize={10.5}
        fontWeight={700}
        fill={emerald}
        textAnchor="end"
        opacity={0.85}
      >
        {zh ? "⚡ 极致性价比甜区（省钱 × 极速）" : "⚡ Fast & Cheap Sweet Spot"}
      </text>

      {/* Panel Titles */}
      <text x={xL0 + 4} y={yT0 - 11} fontSize={12.5} fontWeight={700} fill={gold}>
        {zh ? "主视图 · 每任务成本 × 智力（越右越省 →）" : "Front · Cost/task × Score (Cheaper →)"}
      </text>
      <text x={xR0 + 4} y={yT0 - 11} fontSize={12.5} fontWeight={700} fill={cyan}>
        {zh ? "侧视图 · 任务耗时 × 智力（越右越快 →）" : "Side · Task Time × Score (Faster →)"}
      </text>
      <text x={xL0 + 4} y={yB0 - 11} fontSize={12.5} fontWeight={700} fill={emerald}>
        {zh ? "俯视图 · 成本 × 耗时正交投影（越右上越快越省 ↗）" : "Plan · Cost × Time Ortho (Faster & Cheaper ↗)"}
      </text>
      <text x={xR0 + 4} y={yB0 - 11} fontSize={12.5} fontWeight={700} fill={ink}>
        {hoverKey
          ? zh
            ? "四维实时遥测舱（悬停锁定）"
            : "4D Live Telemetry Inspector"
          : zh
            ? "四维视觉编码与双前沿总览"
            : "4D Encoding & Champions"}
      </text>

      {/* Grid & Axis Ticks */}
      {scoreTicks.map((s) => {
        const y = mapScoreY(s);
        return (
          <g key={`ts-${s}`}>
            <line x1={xL0} x2={xL1} y1={y} y2={y} stroke={grid} />
            <line x1={xR0} x2={xR1} y1={y} y2={y} stroke={grid} />
            {/* Shared horizontal score bridge tick in gutter */}
            <line x1={xL1} x2={xR0} y1={y} y2={y} stroke={grid} strokeDasharray="3 4" opacity={0.55} />
            <text x={xL0 - 8} y={y} fontSize={10.5} fill={text} textAnchor="end" dominantBaseline="central">
              {s}
            </text>
            <text x={(xL1 + xR0) / 2} y={y} fontSize={9.5} fill={text} textAnchor="middle" dominantBaseline="central" opacity={0.75}>
              {s}
            </text>
          </g>
        );
      })}

      {costTicks.map((c) => {
        const x = mapCostX(c);
        return (
          <g key={`tc-${c}`}>
            <line x1={x} x2={x} y1={yT0} y2={yT1} stroke={grid} />
            <line x1={x} x2={x} y1={yB0} y2={yB1} stroke={grid} />
            {/* Vertical alignment projection guide between Front View and Plan View */}
            <line x1={x} x2={x} y1={yT1} y2={yB0} stroke={grid} strokeDasharray="3 4" opacity={0.55} />
            <text x={x} y={yT1 + 15} fontSize={10} fill={text} textAnchor="middle">
              {formatTickPrice(c)}
            </text>
            <text x={x} y={yB1 + 15} fontSize={10} fill={text} textAnchor="middle">
              {formatTickPrice(c)}
            </text>
          </g>
        );
      })}

      {timeTicks.map((t) => {
        const xr = mapTimeX(t);
        const yb = mapPlanTimeY(t);
        return (
          <g key={`tt-${t}`}>
            <line x1={xr} x2={xr} y1={yT0} y2={yT1} stroke={grid} />
            <text x={xr} y={yT1 + 15} fontSize={10} fill={text} textAnchor="middle">
              {formatTickTime(t)}
            </text>
            <line x1={xL0} x2={xL1} y1={yb} y2={yb} stroke={grid} />
            <text x={xL0 - 8} y={yb} fontSize={10} fill={text} textAnchor="end" dominantBaseline="central">
              {formatTickTime(t)}
            </text>
          </g>
        );
      })}

      {/* Frontier Polylines */}
      {costFrontier.length >= 2 && (
        <polyline
          clipPath="url(#triad-p1)"
          fill="none"
          stroke={gold}
          strokeWidth={2.5}
          points={costFrontier
            .map((n) => `${mapCostX(n.cost).toFixed(1)},${mapScoreY(n.score).toFixed(1)}`)
            .join(" ")}
        />
      )}
      {timeFrontier.length >= 2 && (
        <polyline
          clipPath="url(#triad-p2)"
          fill="none"
          stroke={cyan}
          strokeWidth={2.5}
          points={timeFrontier
            .map((n) => `${mapTimeX(n.time).toFixed(1)},${mapScoreY(n.score).toFixed(1)}`)
            .join(" ")}
        />
      )}
      {costTimeFrontier.length >= 2 && (
        <polyline
          clipPath="url(#triad-p3)"
          fill="none"
          stroke={emerald}
          strokeWidth={2.2}
          strokeDasharray="6 4"
          points={costTimeFrontier
            .map((n) => `${mapCostX(n.cost).toFixed(1)},${mapPlanTimeY(n.time).toFixed(1)}`)
            .join(" ")}
        />
      )}

      {/* Synchronized Orthogonal Projection Crosshairs when Hovered */}
      {hoverKey && activeNode && (
        <g pointerEvents="none">
          {/* Vertical beam connecting Panel 1 Cost and Panel 3 Cost */}
          <line
            x1={mapCostX(activeNode.cost)}
            y1={mapScoreY(activeNode.score)}
            x2={mapCostX(activeNode.cost)}
            y2={mapPlanTimeY(activeNode.time)}
            stroke={gold}
            strokeWidth={1.5}
            strokeDasharray="4 3"
            opacity={0.9}
          />
          {/* Horizontal beam connecting Panel 1 Score and Panel 2 Score */}
          <line
            x1={mapCostX(activeNode.cost)}
            y1={mapScoreY(activeNode.score)}
            x2={mapTimeX(activeNode.time)}
            y2={mapScoreY(activeNode.score)}
            stroke={cyan}
            strokeWidth={1.5}
            strokeDasharray="4 3"
            opacity={0.9}
          />
        </g>
      )}

      {/* Plot Glyphs Across All 3 Views */}
      {([
        { id: "p1", clip: "url(#triad-p1)", mapX: (n: Node3D) => mapCostX(n.cost), mapY: (n: Node3D) => mapScoreY(n.score), labels: labelsP1 },
        { id: "p2", clip: "url(#triad-p2)", mapX: (n: Node3D) => mapTimeX(n.time), mapY: (n: Node3D) => mapScoreY(n.score), labels: labelsP2 },
        { id: "p3", clip: "url(#triad-p3)", mapX: (n: Node3D) => mapCostX(n.cost), mapY: (n: Node3D) => mapPlanTimeY(n.time), labels: labelsP3 },
      ] as const).map((panel) => (
        <g key={panel.id} clipPath={panel.clip}>
          {nodes.map((n) => {
            const cx = panel.mapX(n);
            const cy = panel.mapY(n);
            const tFrac = (Math.sqrt(n.tokens) - tokMin) / Math.max(1e-6, tokMax - tokMin);
            const tokR = 9 + tFrac * 10;
            const speedFrac = (n.tps - tpsMin) / Math.max(1e-6, tpsMax - tpsMin);
            const sweep = 35 + speedFrac * 315;
            const isHov = hoverKey === n.key;
            const muted = isMuted(n);
            const logo =
              providerLogoUrl(n.maker, dark ? "dark" : "light") ??
              providerLogoUrl(n.channel, dark ? "dark" : "light");

            return (
              <g
                key={`${panel.id}-${n.key}`}
                transform={`translate(${cx.toFixed(1)} ${cy.toFixed(1)})`}
                opacity={muted ? 0.2 : 1}
                style={{ cursor: "pointer" }}
                onMouseEnter={() => onHover(n.key)}
                onMouseLeave={() => onHover(null)}
                onClick={() => onSelect(n.rows)}
              >
                <circle
                  r={tokR}
                  fill={n.hexColor}
                  fillOpacity={isHov ? 0.26 : 0.11}
                  stroke={n.hexColor}
                  strokeOpacity={0.35}
                  strokeWidth={0.9}
                  strokeDasharray="2 2"
                />
                <path
                  d={describeArc(0, 0, tokR + 2.4, 0, sweep)}
                  fill="none"
                  stroke={n.isFrontier3D ? gold : n.hexColor}
                  strokeWidth={n.isFrontier3D ? 2.2 : 1.7}
                  strokeLinecap="round"
                  opacity={0.9}
                />
                <circle
                  r={n.isFrontier3D ? 9.5 : 7.5}
                  fill={dark ? "#161E2E" : "#FFFFFF"}
                  stroke={isHov ? ink : n.isFrontier3D ? gold : n.hexColor}
                  strokeWidth={isHov ? 2.4 : n.isFrontier3D ? 2 : 1.1}
                />
                {logo ? (
                  <image
                    href={logo}
                    x={-6}
                    y={-6}
                    width={12}
                    height={12}
                    preserveAspectRatio="xMidYMid meet"
                  />
                ) : (
                  <text fontSize={7} fontWeight={700} fill={ink} textAnchor="middle" dominantBaseline="central">
                    {n.vendor.slice(0, 2)}
                  </text>
                )}
              </g>
            );
          })}

          {/* Collision-avoided labels per panel */}
          {nodes.map((n) => {
            const lbl = panel.labels.get(n.key);
            if (!lbl) return null;
            const labelStr = n.isFrontier3D ? `★ ${n.family}` : n.family;
            const estW = labelStr.length * 6.5 + (n.isFrontier3D ? 18 : 8);
            const rxX =
              lbl.anchor === "middle"
                ? lbl.lx - estW / 2
                : lbl.anchor === "start"
                  ? lbl.lx - 4
                  : lbl.lx - estW + 4;
            const boxCx = rxX + estW / 2;
            const boxCy = lbl.ly - 3.5;
            const distFromDot = Math.hypot(boxCx - lbl.ox, boxCy - lbl.oy);
            return (
              <g
                key={`${panel.id}-lbl-${n.key}`}
                style={{ cursor: "pointer" }}
                onMouseEnter={() => onHover(n.key)}
                onMouseLeave={() => onHover(null)}
                onClick={() => onSelect(n.rows)}
              >
                {distFromDot > 24 && (
                  <line
                    x1={lbl.ox}
                    y1={lbl.oy}
                    x2={boxCx}
                    y2={boxCy}
                    stroke={n.isFrontier3D ? gold : text}
                    strokeWidth={0.9}
                    strokeOpacity={0.55}
                    strokeDasharray="2 2"
                  />
                )}
                {n.isFrontier3D ? (
                  <>
                    <rect
                      x={rxX}
                      y={lbl.ly - 11.5}
                      width={estW}
                      height={16}
                      rx={8}
                      fill={goldBg}
                      stroke={gold}
                      strokeWidth={1.1}
                    />
                    <text
                      x={lbl.lx}
                      y={lbl.ly}
                      fontSize={10}
                      fontWeight={700}
                      fill={goldText}
                      textAnchor={lbl.anchor}
                    >
                      {labelStr}
                    </text>
                  </>
                ) : (
                  <text
                    x={lbl.lx}
                    y={lbl.ly}
                    fontSize={9.5}
                    fontWeight={hoverKey === n.key ? 700 : 500}
                    fill={hoverKey === n.key ? ink : text}
                    textAnchor={lbl.anchor}
                    style={{ paintOrder: "stroke", stroke: panelBg, strokeWidth: 3 }}
                  >
                    {labelStr}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      ))}

      {/* Quadrant 4: 4D Live Telemetry & Champions Card */}
      <g>
        {activeNode && (
          <>
            <text x={xR0 + 16} y={yB0 + 24} fontSize={14} fontWeight={700} fill={activeNode.isFrontier3D ? gold : ink}>
              {activeNode.isFrontier3D ? `★ ${activeNode.family}` : activeNode.family}
            </text>
            <text x={xR0 + 16} y={yB0 + 41} fontSize={11} fill={text}>
              {activeNode.planDisplay} · {activeNode.channel}
            </text>

            {/* Normalized 3-axis performance bars */}
            {[
              {
                label: zh ? `成本优势  ${price(activeNode.cost)}` : `Cost Efficiency  ${price(activeNode.cost)}`,
                val: normCost,
                col: gold,
                y: yB0 + 62,
              },
              {
                label: zh
                  ? `速度优势  ${formatTickTime(activeNode.time)} · ${Math.round(activeNode.tps)} tok/s`
                  : `Speed  ${formatTickTime(activeNode.time)} · ${Math.round(activeNode.tps)} tok/s`,
                val: normSpeed,
                col: cyan,
                y: yB0 + 96,
              },
              {
                label: zh
                  ? `智力得分  ${activeNode.score.toFixed(1)} · ${Math.round(activeNode.tokens / 1000)}k tok`
                  : `Intelligence  ${activeNode.score.toFixed(1)} · ${Math.round(activeNode.tokens / 1000)}k tok`,
                val: normScore,
                col: emerald,
                y: yB0 + 130,
              },
            ].map((bar) => {
              const barW = Math.max(120, rightW - 32);
              const fillW = Math.max(6, Math.min(barW, barW * bar.val));
              return (
                <g key={bar.label}>
                  <text x={xR0 + 16} y={bar.y} fontSize={11} fontWeight={600} fill={ink}>
                    {bar.label}
                  </text>
                  <rect x={xR0 + 16} y={bar.y + 7} width={barW} height={7} rx={3.5} fill={grid} />
                  <rect x={xR0 + 16} y={bar.y + 7} width={fillW} height={7} rx={3.5} fill={bar.col} />
                </g>
              );
            })}
          </>
        )}

        <line x1={xR0 + 16} x2={xR1 - 16} y1={yB1 - 54} y2={yB1 - 54} stroke={grid} />
        <text x={xR0 + 16} y={yB1 - 36} fontSize={10.5} fill={text}>
          {zh
            ? "编码：圆心 = 厂商 · 虚线环 ∝ Token 量 · 实线弧 ∝ 生成速度"
            : "Glyph: Core = Vendor · Dashed ring ∝ Tokens · Arc ∝ Speed"}
        </text>
        <text x={xR0 + 16} y={yB1 - 18} fontSize={10.5} fill={gold}>
          {zh
            ? `★ 双前沿冠军 (${dualWinners.length})：${dualWinners.map((d) => d.family).slice(0, 4).join(" · ")}`
            : `★ Dual Winners (${dualWinners.length}): ${dualWinners.map((d) => d.family).slice(0, 4).join(" · ")}`}
        </text>
      </g>
    </svg>
  );
}

/* ============================================================================
 * ENGINE 1 & 3: Three.js WebGL Holographic Deck (+ Optional Frosted Glass Canopy)
 * ========================================================================== */
function ThreeLogoDeckView({
  nodes,
  costFrontier,
  timeFrontier,
  width,
  height,
  dark,
  zh,
  highlight,
  hitSet,
  onSelect,
  hoverKey,
  onHover,
  showCanopy = false,
}: {
  nodes: Node3D[];
  costFrontier: Node3D[];
  timeFrontier: Node3D[];
  width: number;
  height: number;
  dark: boolean;
  zh: boolean;
  highlight: string | null;
  hitSet: Set<string> | null;
  onSelect: (rows: Row[]) => void;
  hoverKey: string | null;
  onHover: (key: string | null) => void;
  showCanopy?: boolean;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const badgeRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const leaderRefs = useRef<Map<string, SVGLineElement>>(new Map());
  const axisRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [ready, setReady] = useState(Boolean(window.THREE));
  const [camPreset, setCamPreset] = useState<"std" | "cost" | "time" | "top" | "orbit">("std");

  const camStateRef = useRef({
    theta: -0.68,
    phi: 1.12,
    radius: 21.2,
    autoOrbit: false,
  });
  const renderTriggerRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!window.THREE) {
        await loadVendorScript("/vendor/three.min.js");
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const applyPreset = (p: "std" | "cost" | "time" | "top" | "orbit") => {
    setCamPreset(p);
    const st = camStateRef.current;
    if (p === "std") {
      st.theta = -0.68;
      st.phi = 1.12;
      st.radius = 21.0;
      st.autoOrbit = false;
    } else if (p === "cost") {
      st.theta = -0.18;
      st.phi = 1.32;
      st.radius = 18.8;
      st.autoOrbit = false;
    } else if (p === "time") {
      st.theta = -1.32;
      st.phi = 1.24;
      st.radius = 18.8;
      st.autoOrbit = false;
    } else if (p === "top") {
      st.theta = -0.68;
      st.phi = 0.32;
      st.radius = 19.6;
      st.autoOrbit = false;
    } else if (p === "orbit") {
      st.autoOrbit = !st.autoOrbit;
    }
    renderTriggerRef.current?.();
  };

  const bounds = useMemo(() => {
    if (!nodes.length) {
      return { xMin: -2.5, xMax: 0.8, yMin: 2.0, yMax: 3.6, zMin: 0, zMax: 68 };
    }
    const xs = nodes.map((n) => Math.log10(n.cost));
    const ys = nodes.map((n) => Math.log10(n.time));
    const zs = nodes.map((n) => n.score);
    return {
      xMin: Math.min(...xs) - 0.16,
      xMax: Math.max(...xs) + 0.16,
      yMin: Math.min(...ys) - 0.12,
      yMax: Math.max(...ys) + 0.12,
      zMin: Math.min(...zs),
      zMax: Math.max(...zs, 62) + 6,
    };
  }, [nodes]);

  const toWorld = (logCost: number, logTime: number, scoreVal: number): [number, number, number] => {
    const W = 13.0;
    const D = 10.0;
    const H = 6.6;
    // Cheaper -> right (+X); Faster -> front (+Z); Score -> up (+Y)
    const wx = ((bounds.xMax - logCost) / Math.max(1e-6, bounds.xMax - bounds.xMin) - 0.5) * W;
    const wz = ((bounds.yMax - logTime) / Math.max(1e-6, bounds.yMax - bounds.yMin) - 0.5) * D;
    const wy = (scoreVal / bounds.zMax) * H - H * 0.42;
    return [wx, wy, wz];
  };

  const axisTickSpecs = useMemo(() => {
    const specs: { id: string; text: string; world: [number, number, number]; color?: string }[] = [];
    const cTicks = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5].filter(
      (v) => Math.log10(v) >= bounds.xMin + 0.05 && Math.log10(v) <= bounds.xMax - 0.05,
    );
    for (const c of cTicks) {
      specs.push({
        id: `cx-${c}`,
        text: formatTickPrice(c),
        world: toWorld(Math.log10(c), bounds.yMin - 0.05, 0),
      });
    }
    const tTicks = [120, 300, 600, 1200, 1800, 3000].filter(
      (v) => Math.log10(v) >= bounds.yMin + 0.04 && Math.log10(v) <= bounds.yMax - 0.04,
    );
    for (const t of tTicks) {
      specs.push({
        id: `ty-${t}`,
        text: formatTickTime(t),
        world: toWorld(bounds.xMax + 0.08, Math.log10(t), 0),
      });
    }
    const zStart = bounds.zMin >= 18 ? 20 : 10;
    for (let s = zStart; s <= 60; s += 10) {
      specs.push({
        id: `sz-${s}`,
        text: `${s} pts`,
        world: toWorld(bounds.xMin - 0.08, bounds.yMin - 0.04, s),
      });
    }
    specs.push({
      id: "title-x",
      text: zh ? "每任务成本 USD（越右越便宜 →）" : "Cost per task · USD (cheaper →)",
      world: toWorld(0.62 * bounds.xMin + 0.38 * bounds.xMax, bounds.yMin - 0.25, 0),
      color: dark ? "#F5B301" : "#B45309",
    });
    specs.push({
      id: "title-y",
      text: zh ? "任务用时（越前越快 →）" : "Task time (faster →)",
      world: toWorld(bounds.xMax + 0.44, 0.44 * bounds.yMin + 0.56 * bounds.yMax, 0),
      color: dark ? "#31C6F0" : "#0284C7",
    });
    return specs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bounds, zh, dark]);

  useEffect(() => {
    if (!ready || !mountRef.current || !window.THREE) return;
    const THREE = window.THREE;
    const container = mountRef.current;
    container.innerHTML = "";

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(dark ? 0x090d14 : 0xf1f5f9);

    const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 200);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height);
    container.appendChild(renderer.domElement);

    // Ambient + directional lights
    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(12, 22, 16);
    scene.add(dirLight);

    const floorY = toWorld(bounds.xMin, bounds.yMin, 0)[1];

    // Holographic floor platform + grid lines
    const floorGeo = new THREE.PlaneGeometry(13.0, 10.0);
    const floorMat = new THREE.MeshBasicMaterial({
      color: dark ? 0x101724 : 0xe2e8f0,
      transparent: true,
      opacity: 0.72,
      side: THREE.DoubleSide,
    });
    const floorMesh = new THREE.Mesh(floorGeo, floorMat);
    floorMesh.rotation.x = -Math.PI / 2;
    floorMesh.position.y = floorY;
    scene.add(floorMesh);

    const gridMat = new THREE.LineBasicMaterial({
      color: dark ? 0x26354d : 0xcbd5e1,
      transparent: true,
      opacity: 0.75,
    });
    const cTicks = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5].filter(
      (v) => Math.log10(v) >= bounds.xMin && Math.log10(v) <= bounds.xMax,
    );
    for (const c of cTicks) {
      const p0 = toWorld(Math.log10(c), bounds.yMax, 0);
      const p1 = toWorld(Math.log10(c), bounds.yMin, 0);
      const g = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...p0),
        new THREE.Vector3(...p1),
      ]);
      scene.add(new THREE.Line(g, gridMat));
    }
    const tTicks = [60, 120, 300, 600, 1200, 1800, 3000].filter(
      (v) => Math.log10(v) >= bounds.yMin && Math.log10(v) <= bounds.yMax,
    );
    for (const t of tTicks) {
      const p0 = toWorld(bounds.xMax, Math.log10(t), 0);
      const p1 = toWorld(bounds.xMin, Math.log10(t), 0);
      const g = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...p0),
        new THREE.Vector3(...p1),
      ]);
      scene.add(new THREE.Line(g, gridMat));
    }

    // Back score wall grid lines & vertical score axis posts
    for (let s = 10; s <= 60; s += 10) {
      const a = toWorld(bounds.xMax, bounds.yMax, s);
      const b = toWorld(bounds.xMin, bounds.yMax, s);
      const c = toWorld(bounds.xMin, bounds.yMin, s);
      const g = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...a),
        new THREE.Vector3(...b),
        new THREE.Vector3(...c),
      ]);
      scene.add(new THREE.Line(g, gridMat));

      const tickOut = toWorld(bounds.xMin - 0.03, bounds.yMin - 0.02, s);
      const tickGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...c),
        new THREE.Vector3(...tickOut),
      ]);
      scene.add(new THREE.Line(tickGeo, gridMat));
    }
    const wallPostMat = new THREE.LineBasicMaterial({
      color: dark ? 0x3b4d6b : 0x94a3b8,
      transparent: true,
      opacity: 0.78,
    });
    for (const [px, py] of [
      [bounds.xMax, bounds.yMax],
      [bounds.xMin, bounds.yMax],
      [bounds.xMin, bounds.yMin],
    ]) {
      const pBot = toWorld(px, py, 0);
      const pTop = toWorld(px, py, 64);
      const pg = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...pBot),
        new THREE.Vector3(...pTop),
      ]);
      scene.add(new THREE.Line(pg, wallPostMat));
    }

    // Optional frosted translucent capability contour terrain (Engine ①)
    if (showCanopy && nodes.length >= 4) {
      const G = 48;
      const spanX = Math.max(1e-6, bounds.xMax - bounds.xMin);
      const spanY = Math.max(1e-6, bounds.yMax - bounds.yMin);
      const nCoords = nodes.map((n) => ({
        lx: Math.log10(n.cost),
        ly: Math.log10(n.time),
        score: n.score,
      }));
      const minS = Math.min(...nodes.map((n) => n.score), 15);
      const maxS = Math.max(...nodes.map((n) => n.score), 60);

      const positions: number[] = [];
      const colors: number[] = [];
      const indices: number[] = [];
      const scoreGrid: number[][] = [];
      const worldGrid: [number, number, number][][] = [];

      const cLow = new THREE.Color(dark ? 0x0ea5e9 : 0x38bdf8);
      const cMid = new THREE.Color(dark ? 0x10b981 : 0x34d399);
      const cHigh = new THREE.Color(dark ? 0xfbbf24 : 0xd97706);

      for (let j = 0; j <= G; j++) {
        const ly = bounds.yMin + (spanY * j) / G;
        const sRow: number[] = [];
        const wRow: [number, number, number][] = [];
        for (let i = 0; i <= G; i++) {
          const lx = bounds.xMin + (spanX * i) / G;
          let wSum = 0;
          let zSum = 0;
          for (const nd of nCoords) {
            const dx = (lx - nd.lx) / spanX;
            const dy = (ly - nd.ly) / spanY;
            const d2 = dx * dx + dy * dy;
            // Tight local IDW so surface hugs real peaks/valleys without artificial corner plateaus
            const w = 1.0 / Math.pow(d2 + 0.0028, 1.35);
            wSum += w;
            zSum += w * nd.score;
          }
          const estScore = zSum / Math.max(1e-6, wSum);
          const worldPt = toWorld(lx, ly, estScore);
          sRow.push(estScore);
          wRow.push(worldPt);
          positions.push(worldPt[0], worldPt[1], worldPt[2]);

          const tRaw = Math.max(0, Math.min(1, (estScore - minS) / Math.max(1, maxS - minS)));
          const band = Math.floor(estScore / 5) * 5;
          const tBand = Math.max(0, Math.min(1, (band - minS) / Math.max(1, maxS - minS)));
          const t = tRaw * 0.72 + tBand * 0.28;
          const col =
            t < 0.5
              ? cLow.clone().lerp(cMid, t * 2)
              : cMid.clone().lerp(cHigh, (t - 0.5) * 2);
          colors.push(col.r, col.g, col.b);
        }
        scoreGrid.push(sRow);
        worldGrid.push(wRow);
      }

      for (let j = 0; j < G; j++) {
        for (let i = 0; i < G; i++) {
          const a = j * (G + 1) + i;
          const b = a + 1;
          const c = a + (G + 1);
          const d = c + 1;
          indices.push(a, b, c, b, d, c);
        }
      }

      const canopyGeo = new THREE.BufferGeometry();
      canopyGeo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      canopyGeo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
      canopyGeo.setIndex(indices);
      canopyGeo.computeVertexNormals();

      const canopyMat = new THREE.MeshStandardMaterial({
        vertexColors: true,
        transparent: true,
        opacity: dark ? 0.22 : 0.23,
        roughness: 0.4,
        metalness: 0.1,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      scene.add(new THREE.Mesh(canopyGeo, canopyMat));

      // True 3D iso-score contour lines directly ON the terrain surface + floor projections
      const isoLevels = [
        { level: 20, major: true, col: dark ? 0x38bdf8 : 0x0284c7 },
        { level: 25, major: false, col: dark ? 0x38bdf8 : 0x0284c7 },
        { level: 30, major: true, col: dark ? 0x2dd4bf : 0x0d9488 },
        { level: 35, major: false, col: dark ? 0x34d399 : 0x059669 },
        { level: 40, major: true, col: dark ? 0x34d399 : 0x059669 },
        { level: 45, major: false, col: dark ? 0xa3e635 : 0x65a30d },
        { level: 50, major: true, col: dark ? 0xfbbf24 : 0xd97706 },
        { level: 55, major: false, col: dark ? 0xf59e0b : 0xb45309 },
        { level: 60, major: true, col: dark ? 0xfde047 : 0xb45309 },
      ];
      for (const iso of isoLevels) {
        const surfSegs: number[] = [];
        const floorSegs: number[] = [];
        const isoY = toWorld(bounds.xMin, bounds.yMin, iso.level)[1] + 0.012;
        for (let j = 0; j < G; j++) {
          for (let i = 0; i < G; i++) {
            const cell = [
              { x: worldGrid[j][i][0], z: worldGrid[j][i][2], v: scoreGrid[j][i] },
              { x: worldGrid[j][i + 1][0], z: worldGrid[j][i + 1][2], v: scoreGrid[j][i + 1] },
              { x: worldGrid[j + 1][i + 1][0], z: worldGrid[j + 1][i + 1][2], v: scoreGrid[j + 1][i + 1] },
              { x: worldGrid[j + 1][i][0], z: worldGrid[j + 1][i][2], v: scoreGrid[j + 1][i] },
            ];
            const crossings: [number, number][] = [];
            for (let e = 0; e < 4; e++) {
              const p1 = cell[e];
              const p2 = cell[(e + 1) % 4];
              if ((p1.v < iso.level && p2.v >= iso.level) || (p2.v < iso.level && p1.v >= iso.level)) {
                const t = (iso.level - p1.v) / (p2.v - p1.v);
                crossings.push([p1.x + (p2.x - p1.x) * t, p1.z + (p2.z - p1.z) * t]);
              }
            }
            if (crossings.length >= 2) {
              surfSegs.push(
                crossings[0][0], isoY, crossings[0][1],
                crossings[1][0], isoY, crossings[1][1],
              );
              if (iso.major) {
                floorSegs.push(
                  crossings[0][0], floorY + 0.018, crossings[0][1],
                  crossings[1][0], floorY + 0.018, crossings[1][1],
                );
              }
            }
          }
        }
        if (surfSegs.length > 0) {
          const surfGeo = new THREE.BufferGeometry();
          surfGeo.setAttribute("position", new THREE.Float32BufferAttribute(surfSegs, 3));
          const surfMat = new THREE.LineBasicMaterial({
            color: iso.col,
            transparent: true,
            opacity: iso.major ? (dark ? 0.78 : 0.68) : dark ? 0.30 : 0.26,
          });
          scene.add(new THREE.LineSegments(surfGeo, surfMat));
        }
        if (floorSegs.length > 0) {
          const isoGeo = new THREE.BufferGeometry();
          isoGeo.setAttribute("position", new THREE.Float32BufferAttribute(floorSegs, 3));
          const isoMat = new THREE.LineBasicMaterial({
            color: iso.col,
            transparent: true,
            opacity: dark ? 0.36 : 0.30,
          });
          scene.add(new THREE.LineSegments(isoGeo, isoMat));
        }
      }
    }

    // Token scale for floor rings
    const tokMin = Math.min(...nodes.map((n) => Math.sqrt(n.tokens)), 100);
    const tokMax = Math.max(...nodes.map((n) => Math.sqrt(n.tokens)), 600);

    // Add each model's floor token halo ring + vertical laser fiber + 3D core orb
    for (const n of nodes) {
      const [wx, wy, wz] = toWorld(Math.log10(n.cost), Math.log10(n.time), n.score);
      const tFrac =
        (Math.sqrt(n.tokens) - tokMin) / Math.max(1e-6, tokMax - tokMin);
      const ringR = 0.14 + tFrac * 0.36;

      // Floor token ring (area proportional to tokens per task)
      const ringGeo = new THREE.RingGeometry(ringR * 0.65, ringR, 32);
      const ringMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(n.hexColor),
        transparent: true,
        opacity: n.isFrontier3D ? 0.58 : 0.28,
        side: THREE.DoubleSide,
      });
      const ringMesh = new THREE.Mesh(ringGeo, ringMat);
      ringMesh.rotation.x = -Math.PI / 2;
      ringMesh.position.set(wx, floorY + 0.01, wz);
      scene.add(ringMesh);

      if (n.isFrontier3D) {
        const outerHaloGeo = new THREE.RingGeometry(ringR * 1.14, ringR * 1.34, 36);
        const outerHaloMat = new THREE.MeshBasicMaterial({
          color: dark ? 0xfbbf24 : 0xd97706,
          transparent: true,
          opacity: dark ? 0.72 : 0.62,
          side: THREE.DoubleSide,
        });
        const outerHalo = new THREE.Mesh(outerHaloGeo, outerHaloMat);
        outerHalo.rotation.x = -Math.PI / 2;
        outerHalo.position.set(wx, floorY + 0.012, wz);
        scene.add(outerHalo);
      }

      // Vertical laser stem
      const stemGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(wx, floorY, wz),
        new THREE.Vector3(wx, wy, wz),
      ]);
      const stemMat = new THREE.LineBasicMaterial({
        color: n.isFrontier3D ? (dark ? 0xfbbf24 : 0xd97706) : new THREE.Color(n.hexColor),
        transparent: true,
        opacity: n.isFrontier3D ? 0.68 : dark ? 0.36 : 0.28,
      });
      scene.add(new THREE.Line(stemGeo, stemMat));

      // Glowing 3D anchor sphere
      const spGeo = new THREE.SphereGeometry(n.isFrontier3D ? 0.13 : 0.085, 20, 20);
      const spMat = new THREE.MeshStandardMaterial({
        color: new THREE.Color(n.hexColor),
        emissive: new THREE.Color(n.hexColor),
        emissiveIntensity: n.isFrontier3D ? 0.45 : 0.2,
        roughness: 0.25,
        metalness: 0.2,
      });
      const sp = new THREE.Mesh(spGeo, spMat);
      sp.position.set(wx, wy, wz);
      scene.add(sp);

      if (n.isFrontier3D) {
        const crownGeo = new THREE.TorusGeometry(0.19, 0.015, 8, 32);
        const crownMat = new THREE.MeshStandardMaterial({
          color: dark ? 0xfbbf24 : 0xd97706,
          emissive: dark ? 0xf59e0b : 0xb45309,
          emissiveIntensity: dark ? 0.48 : 0.18,
          roughness: 0.25,
          metalness: 0.4,
        });
        const crown = new THREE.Mesh(crownGeo, crownMat);
        crown.position.set(wx, wy, wz);
        crown.rotation.set(Math.PI / 2.6, 0.35, 0);
        scene.add(crown);
      }
    }

    // Smooth monotonic Pareto frontier splines (avoids jagged cross-space 3D zigzag)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const addSmoothSpline = (
      pts: any[],
      radius: number,
      hex: number,
      emissiveHex: number,
      emissiveInt: number,
      opacity: number,
    ) => {
      if (pts.length < 2) return;
      const curve =
        pts.length === 2
          ? new THREE.LineCurve3(pts[0], pts[1])
          : new THREE.CatmullRomCurve3(pts, false, "centripetal", 0.45);
      const segments = Math.max(28, pts.length * 14);
      const geo = new THREE.TubeGeometry(curve, segments, radius, 10, false);
      const mat = new THREE.MeshStandardMaterial({
        color: hex,
        emissive: emissiveHex,
        emissiveIntensity: emissiveInt,
        roughness: 0.32,
        metalness: 0.32,
        transparent: opacity < 1,
        opacity,
      });
      scene.add(new THREE.Mesh(geo, mat));
    };

    if (costFrontier.length >= 2) {
      const cPts = costFrontier.map((n) => {
        const [wx, wy, wz] = toWorld(Math.log10(n.cost), Math.log10(n.time), n.score);
        return new THREE.Vector3(wx, wy, wz);
      });
      const cFloorPts = cPts.map((p) => new THREE.Vector3(p.x, floorY + 0.015, p.z));
      addSmoothSpline(
        cPts,
        0.025,
        dark ? 0xfbbf24 : 0xd97706,
        dark ? 0xf59e0b : 0xb45309,
        dark ? 0.42 : 0.16,
        0.9,
      );
      addSmoothSpline(
        cFloorPts,
        0.01,
        dark ? 0xfbbf24 : 0xd97706,
        dark ? 0xf59e0b : 0xb45309,
        0.15,
        dark ? 0.28 : 0.22,
      );
    }

    if (timeFrontier.length >= 2) {
      const tPts = timeFrontier.map((n) => {
        const [wx, wy, wz] = toWorld(Math.log10(n.cost), Math.log10(n.time), n.score);
        return new THREE.Vector3(wx, wy - 0.028, wz);
      });
      const tFloorPts = tPts.map((p) => new THREE.Vector3(p.x, floorY + 0.011, p.z));
      addSmoothSpline(
        tPts,
        0.017,
        dark ? 0x38bdf8 : 0x0284c7,
        dark ? 0x0ea5e9 : 0x0369a1,
        dark ? 0.36 : 0.16,
        0.78,
      );
      addSmoothSpline(
        tFloorPts,
        0.008,
        dark ? 0x38bdf8 : 0x0284c7,
        dark ? 0x0ea5e9 : 0x0369a1,
        0.12,
        dark ? 0.22 : 0.18,
      );
    }

    let raf = 0;
    const tmpVec = new THREE.Vector3();

    const renderFrame = () => {
      cancelAnimationFrame(raf);
      const st = camStateRef.current;
      if (st.autoOrbit) {
        st.theta += 0.0045;
      }
      const cx = st.radius * Math.sin(st.phi) * Math.sin(st.theta);
      const cy = st.radius * Math.cos(st.phi);
      const cz = st.radius * Math.sin(st.phi) * Math.cos(st.theta);
      camera.position.set(cx, cy, cz);
      camera.lookAt(0, -1.32, 0);
      camera.updateMatrixWorld();

      renderer.render(scene, camera);

      // Project floating HTML Provider Logo Badges with 2D collision avoidance
      const projected = nodes
        .map((n) => {
          const [wx, wy, wz] = toWorld(Math.log10(n.cost), Math.log10(n.time), n.score);
          tmpVec.set(wx, wy, wz);
          const dist = tmpVec.distanceTo(camera.position);
          tmpVec.project(camera);
          const sx = (tmpVec.x * 0.5 + 0.5) * width;
          const sy = (-tmpVec.y * 0.5 + 0.5) * height;
          const scale = Math.max(0.76, Math.min(1.12, 16.5 / Math.max(8, dist)));
          return { n, sx, sy, dist, scale };
        })
        .sort((a, b) => (a.n.isFrontier3D !== b.n.isFrontier3D ? (a.n.isFrontier3D ? -1 : 1) : b.n.score - a.n.score));

      const placedBoxes: [number, number, number, number][] = [
        [0, 0, Math.min(440, width * 0.42), 54],
        [Math.max(0, width - 255), 0, width, showCanopy ? 92 : 70],
        [0, height - 46, Math.min(460, width * 0.48), height],
      ];
      const badgeOffsets: [number, number][] = [];
      for (const dy of [0, -24, 24, -48, 48, -72, 72, -96, 96]) {
        for (const dx of [0, -75, 75, -145, 145, -210, 210]) {
          badgeOffsets.push([dx, dy]);
        }
      }
      badgeOffsets.sort((a, b) => a[0] * a[0] + a[1] * a[1] * 2.8 - (b[0] * b[0] + b[1] * b[1] * 2.8));

      for (const item of projected) {
        const { n, sx, sy, dist, scale } = item;
        const el = badgeRefs.current.get(n.key);
        if (!el) continue;
        const bw = (n.family.length * (n.isFrontier3D ? 7.4 : 6.9) + (n.isFrontier3D ? 46 : 32)) * scale;
        const bh = 24 * scale;
        let finalX = Math.max(bw / 2 + 8, Math.min(width - bw / 2 - 8, sx));
        let finalY = Math.max(bh + 10, Math.min(height - 12, sy - 8));
        let bestScore = Infinity;
        let placed = false;
        for (const [dx, dy] of badgeOffsets) {
          const cxTest = Math.max(bw / 2 + 8, Math.min(width - bw / 2 - 8, sx + dx));
          const cyTest = Math.max(bh + 10, Math.min(height - 12, sy - 8 + dy));
          const box: [number, number, number, number] = [
            cxTest - bw / 2 - 3,
            cyTest - bh - 3,
            cxTest + bw / 2 + 3,
            cyTest + 3,
          ];
          let overlapArea = 0;
          for (const b of placedBoxes) {
            const ox = Math.max(0, Math.min(box[2], b[2]) - Math.max(box[0], b[0]));
            const oy = Math.max(0, Math.min(box[3], b[3]) - Math.max(box[1], b[1]));
            if (ox > 0 && oy > 0) overlapArea += ox * oy;
          }
          if (overlapArea === 0) {
            finalX = cxTest;
            finalY = cyTest;
            placedBoxes.push(box);
            placed = true;
            break;
          }
          if (overlapArea < bestScore) {
            bestScore = overlapArea;
            finalX = cxTest;
            finalY = cyTest;
          }
        }
        if (!placed) {
          placedBoxes.push([finalX - bw / 2, finalY - bh, finalX + bw / 2, finalY]);
        }
        el.style.transformOrigin = "50% 100%";
        el.style.transform = `translate3d(${finalX.toFixed(1)}px, ${finalY.toFixed(1)}px, 0) translate(-50%, -100%) scale(${scale.toFixed(3)})`;
        el.style.zIndex = String(Math.round(2000 - dist * 40) + (n.isFrontier3D ? 500 : 0));

        const leaderEl = leaderRefs.current.get(n.key);
        if (leaderEl) {
          const anchorY = finalY - bh * 0.5;
          const shifted = Math.hypot(finalX - sx, finalY - (sy - 8));
          if (shifted > 14) {
            leaderEl.setAttribute("x1", sx.toFixed(1));
            leaderEl.setAttribute("y1", sy.toFixed(1));
            leaderEl.setAttribute("x2", finalX.toFixed(1));
            leaderEl.setAttribute("y2", anchorY.toFixed(1));
            leaderEl.style.opacity = "0.55";
          } else {
            leaderEl.style.opacity = "0";
          }
        }
      }

      // Project 3D axis tick labels
      for (const spec of axisTickSpecs) {
        const el = axisRefs.current.get(spec.id);
        if (!el) continue;
        tmpVec.set(...spec.world);
        tmpVec.project(camera);
        const sx = (tmpVec.x * 0.5 + 0.5) * width;
        const sy = (-tmpVec.y * 0.5 + 0.5) * height;
        el.style.transform = `translate3d(${sx.toFixed(1)}px, ${sy.toFixed(1)}px, 0) translate(-50%, -50%)`;
      }

      if (st.autoOrbit) {
        raf = requestAnimationFrame(renderFrame);
      }
    };

    renderTriggerRef.current = renderFrame;
    renderFrame();
    const timer = window.setTimeout(renderFrame, 60);

    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(timer);
      renderTriggerRef.current = null;
      renderer.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, nodes, costFrontier, timeFrontier, width, height, dark, bounds, axisTickSpecs, showCanopy]);

  const dragRef = useRef<{ x: number; y: number; theta: number; phi: number } | null>(null);

  return (
    <div style={{ position: "relative", width: "100%", height, overflow: "hidden" }}>
      {/* Camera preset mini-bar */}
      <div
        style={{
          position: "absolute",
          top: 12,
          left: 14,
          zIndex: 3000,
          display: "flex",
          gap: 6,
          background: dark ? "rgba(15,23,42,0.82)" : "rgba(255,255,255,0.88)",
          padding: "5px 8px",
          borderRadius: 8,
          border: "1px solid rgba(148,163,184,0.22)",
          backdropFilter: "blur(6px)",
        }}
      >
        <div className="segmented">
          <button
            type="button"
            aria-pressed={camPreset === "std"}
            onClick={() => applyPreset("std")}
          >
            {zh ? "全息标准机位" : "Standard"}
          </button>
          <button
            type="button"
            aria-pressed={camPreset === "cost"}
            onClick={() => applyPreset("cost")}
          >
            {zh ? "正看成本" : "Cost"}
          </button>
          <button
            type="button"
            aria-pressed={camPreset === "time"}
            onClick={() => applyPreset("time")}
          >
            {zh ? "侧看耗时" : "Time"}
          </button>
          <button
            type="button"
            aria-pressed={camPreset === "top"}
            onClick={() => applyPreset("top")}
          >
            {zh ? "俯视 Token 环" : "Top Token Rings"}
          </button>
          <button
            type="button"
            aria-pressed={camPreset === "orbit"}
            onClick={() => applyPreset("orbit")}
          >
            {zh ? "⟳ 360° 巡航" : "⟳ Orbit"}
          </button>
        </div>
      </div>

      {/* Top-right visual line & contour legend */}
      <div
        style={{
          position: "absolute",
          top: 12,
          right: 14,
          zIndex: 3000,
          display: "flex",
          flexDirection: "column",
          gap: 5,
          fontSize: 11,
          color: dark ? "#CBD5E1" : "#334155",
          background: dark ? "rgba(15,23,42,0.84)" : "rgba(255,255,255,0.90)",
          padding: "8px 11px",
          borderRadius: 8,
          border: "1px solid rgba(148,163,184,0.22)",
          backdropFilter: "blur(6px)",
          pointerEvents: "none",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ width: 18, height: 3, borderRadius: 2, background: dark ? "#FBBF24" : "#D97706", display: "inline-block" }} />
          <span>{zh ? "黄线：成本 × 智力前沿" : "Gold line: Cost × Score frontier"}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ width: 18, height: 3, borderRadius: 2, background: dark ? "#38BDF8" : "#0284C7", display: "inline-block" }} />
          <span>{zh ? "蓝线：耗时 × 智力前沿" : "Cyan line: Time × Score frontier"}</span>
        </div>
        {showCanopy && (
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ width: 18, height: 2, borderTop: `2px dashed ${dark ? "#34D399" : "#059669"}`, display: "inline-block" }} />
            <span>{zh ? "曲面环线：5 / 10 分能力等高线" : "Iso-rings: 5 / 10 pts score contours"}</span>
          </div>
        )}
      </div>

      {/* Legend badge in bottom-left */}
      <div
        style={{
          position: "absolute",
          bottom: 12,
          left: 14,
          zIndex: 3000,
          fontSize: 11.5,
          color: dark ? "#9AA5B8" : "#475569",
          background: dark ? "rgba(15,23,42,0.82)" : "rgba(255,255,255,0.88)",
          padding: "6px 12px",
          borderRadius: 8,
          border: "1px solid rgba(148,163,184,0.2)",
        }}
      >
        {zh
          ? "拖拽旋转视角 · 滚轮缩放 · 地面光环面积 ∝ 每任务 Token · ★ 金徽章 = 三维帕累托最优"
          : "Drag to orbit · Scroll to zoom · Floor ring ∝ Tokens/task · ★ Gold = 3D Pareto"}
      </div>

      {/* Three.js Canvas Mount */}
      <div
        ref={mountRef}
        style={{ position: "relative", zIndex: 1, width: "100%", height: "100%", cursor: "grab", touchAction: "none" }}
        onPointerDown={(e) => {
          camStateRef.current.autoOrbit = false;
          e.currentTarget.setPointerCapture(e.pointerId);
          dragRef.current = {
            x: e.clientX,
            y: e.clientY,
            theta: camStateRef.current.theta,
            phi: camStateRef.current.phi,
          };
        }}
        onPointerMove={(e) => {
          if (!dragRef.current) return;
          const dx = e.clientX - dragRef.current.x;
          const dy = e.clientY - dragRef.current.y;
          camStateRef.current.theta = dragRef.current.theta - dx * 0.0075;
          camStateRef.current.phi = Math.max(
            0.22,
            Math.min(1.48, dragRef.current.phi - dy * 0.0065),
          );
          renderTriggerRef.current?.();
        }}
        onPointerUp={(e) => {
          dragRef.current = null;
          try {
            e.currentTarget.releasePointerCapture(e.pointerId);
          } catch {
            // ignore
          }
        }}
        onWheel={(e) => {
          e.preventDefault();
          const factor = e.deltaY > 0 ? 1.08 : 1 / 1.08;
          camStateRef.current.radius = Math.max(
            9.5,
            Math.min(28, camStateRef.current.radius * factor),
          );
          renderTriggerRef.current?.();
        }}
      />

      {/* 3D Axis Tick Labels */}
      <div style={{ position: "absolute", inset: 0, zIndex: 5, pointerEvents: "none" }}>
        {axisTickSpecs.map((spec) => (
          <div
            key={spec.id}
            ref={(el) => {
              if (el) axisRefs.current.set(spec.id, el);
              else axisRefs.current.delete(spec.id);
            }}
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              fontSize: spec.color ? 12 : 10.5,
              fontWeight: spec.color ? 700 : 500,
              color: spec.color ?? (dark ? "#94A3B8" : "#475569"),
              whiteSpace: "nowrap",
              textShadow: dark ? "0 1px 4px #000" : "0 1px 3px #fff",
            }}
          >
            {spec.text}
          </div>
        ))}
      </div>

      {/* Leader lines for collision-shifted 3D badges */}
      <svg
        width={width}
        height={height}
        style={{ position: "absolute", inset: 0, zIndex: 8, pointerEvents: "none" }}
      >
        {nodes.map((n) => (
          <line
            key={`ldr-${n.key}`}
            ref={(el) => {
              if (el) leaderRefs.current.set(n.key, el);
              else leaderRefs.current.delete(n.key);
            }}
            stroke={n.isFrontier3D ? (dark ? "#FBBF24" : "#D97706") : n.hexColor}
            strokeWidth={1}
            strokeDasharray="2 2"
            style={{ opacity: 0 }}
          />
        ))}
      </svg>

      {/* Floating HTML Provider-Logo Badges in 3D Space */}
      <div style={{ position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none" }}>
        {nodes.map((n) => {
          const logo =
            providerLogoUrl(n.maker, dark ? "dark" : "light") ??
            providerLogoUrl(n.channel, dark ? "dark" : "light");
          const muted =
            (highlight !== null && n.channel !== highlight) ||
            (hitSet !== null && !hitSet.has(n.key));
          const isHovered = hoverKey === n.key;
          const showText =
            stateLabelVisible(n, isHovered, hitSet) || nodes.length <= 32;

          return (
            <div
              key={n.key}
              ref={(el) => {
                if (el) badgeRefs.current.set(n.key, el);
                else badgeRefs.current.delete(n.key);
              }}
              onClick={() => onSelect(n.rows)}
              onMouseEnter={() => onHover(n.key)}
              onMouseLeave={() => onHover(null)}
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                pointerEvents: "auto",
                cursor: "pointer",
                opacity: muted ? 0.22 : 1,
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: n.isFrontier3D ? "3.5px 9px 3.5px 4px" : "2px 6px 2px 3px",
                borderRadius: 999,
                background: n.isFrontier3D
                  ? dark
                    ? "linear-gradient(135deg, rgba(69, 44, 8, 0.96) 0%, rgba(28, 20, 8, 0.96) 100%)"
                    : "linear-gradient(135deg, #FEF3C7 0%, #FDE68A 100%)"
                  : dark
                    ? "rgba(15, 23, 42, 0.78)"
                    : "rgba(255, 255, 255, 0.88)",
                border: isHovered
                  ? dark
                    ? "1.8px solid #FFFFFF"
                    : "1.8px solid #0F172A"
                  : n.isFrontier3D
                    ? dark
                      ? "1.6px solid #FBBF24"
                      : "1.6px solid #D97706"
                    : dark
                      ? `1px solid ${n.hexColor}99`
                      : `1px solid ${n.hexColor}AA`,
                boxShadow: isHovered
                  ? dark
                    ? "0 0 16px rgba(255,255,255,0.45), 0 4px 12px rgba(0,0,0,0.55)"
                    : "0 0 0 2px rgba(15,23,42,0.18), 0 6px 14px rgba(15,23,42,0.18)"
                  : n.isFrontier3D
                    ? dark
                      ? "0 0 14px rgba(251, 191, 36, 0.36), 0 4px 12px rgba(0, 0, 0, 0.55)"
                      : "0 4px 12px rgba(217, 119, 6, 0.28), 0 1px 2px rgba(120, 53, 15, 0.14)"
                    : dark
                      ? "0 2px 6px rgba(0,0,0,0.35)"
                      : "0 2px 6px rgba(15,23,42,0.10)",
                whiteSpace: "nowrap",
              }}
            >
              <span
                style={{
                  width: n.isFrontier3D ? 20 : 16,
                  height: n.isFrontier3D ? 20 : 16,
                  borderRadius: "50%",
                  background: dark ? "#1E293B" : "#F8FAFC",
                  border: n.isFrontier3D
                    ? dark
                      ? "1px solid rgba(251, 191, 36, 0.65)"
                      : "1px solid rgba(180, 83, 9, 0.45)"
                    : "none",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  overflow: "hidden",
                  flexShrink: 0,
                }}
              >
                {logo ? (
                  <img
                    src={logo}
                    alt=""
                    style={{ width: "78%", height: "78%", objectFit: "contain" }}
                  />
                ) : (
                  <span style={{ fontSize: 8, fontWeight: 700 }}>{n.vendor.slice(0, 2)}</span>
                )}
              </span>
              {showText && (
                <span
                  style={{
                    fontSize: n.isFrontier3D ? 11.4 : 10,
                    fontWeight: n.isFrontier3D ? 700 : 500,
                    color: n.isFrontier3D
                      ? dark
                        ? "#FEF08A"
                        : "#78350F"
                      : dark
                        ? "#CBD5E1"
                        : "#334155",
                    letterSpacing: n.isFrontier3D ? "0.01em" : undefined,
                  }}
                >
                  {n.isFrontier3D && (
                    <span
                      style={{
                        color: dark ? "#FBBF24" : "#B45309",
                        marginRight: 3,
                        fontSize: 10.5,
                      }}
                    >
                      ★
                    </span>
                  )}
                  {n.family}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function stateLabelVisible(
  n: Node3D,
  isHovered: boolean,
  hitSet: Set<string> | null,
): boolean {
  return n.isFrontier3D || isHovered || Boolean(hitSet && hitSet.has(n.key));
}

/* ============================================================================
 * ENGINE 4: Native SVG 4D Badges + Dual-Frontier Linked Split View
 * ========================================================================== */
function NativeSvg4DSplitView({
  nodes,
  costFrontier,
  timeFrontier,
  width,
  height,
  dark,
  zh,
  highlight,
  hitSet,
  hoverKey,
  onHover,
  onSelect,
}: {
  nodes: Node3D[];
  costFrontier: Node3D[];
  timeFrontier: Node3D[];
  dualWinners?: Node3D[];
  width: number;
  height: number;
  dark: boolean;
  zh: boolean;
  highlight: string | null;
  hitSet: Set<string> | null;
  hoverKey: string | null;
  onHover: (key: string | null) => void;
  onSelect: (rows: Row[]) => void;
}) {
  const padL = 54;
  const padR = 24;
  const padT = 36;
  const padB = 56;
  const spineW = Math.max(96, Math.round(width * 0.11));
  const panelW = Math.max(220, Math.floor((width - padL - padR - spineW) / 2));
  const plotH = height - padT - padB;

  const leftX0 = padL;
  const leftX1 = padL + panelW;
  const rightX0 = leftX1 + spineW;
  const rightX1 = rightX0 + panelW;

  const xs = nodes.map((n) => Math.log10(n.cost));
  const ys = nodes.map((n) => Math.log10(n.time));
  const zs = nodes.map((n) => n.score);

  const cMin = Math.min(...xs, -2.6) - 0.16;
  const cMax = Math.max(...xs, 0.8) + 0.16;
  const tMin = Math.min(...ys, 2.0) - 0.12;
  const tMax = Math.max(...ys, 3.55) + 0.12;
  const sMin = Math.max(0, Math.min(...zs, 5) - 6);
  const sMax = Math.max(...zs, 65) + 6;

  const tokMin = Math.min(...nodes.map((n) => Math.sqrt(n.tokens)), 100);
  const tokMax = Math.max(...nodes.map((n) => Math.sqrt(n.tokens)), 600);
  const tpsMin = Math.min(...nodes.map((n) => n.tps), 30);
  const tpsMax = Math.max(...nodes.map((n) => n.tps), 900);

  const mapCostX = (cost: number) =>
    leftX0 + ((cMax - Math.log10(cost)) / Math.max(1e-6, cMax - cMin)) * panelW;
  const mapTimeX = (time: number) =>
    rightX0 + ((tMax - Math.log10(time)) / Math.max(1e-6, tMax - tMin)) * panelW;
  const mapScoreY = (score: number) =>
    padT + ((sMax - score) / Math.max(1e-6, sMax - sMin)) * plotH;

  const bg = dark ? "#0B0E14" : "#F8FAFC";
  const panelBg = dark ? "#111722" : "#FFFFFF";
  const grid = dark ? "#1E2634" : "#E2E8F0";
  const gridMajor = dark ? "#33405A" : "#CBD5E1";
  const ink = dark ? "#F4F7FC" : "#0F172A";
  const text = dark ? "#9AA5B8" : "#475569";

  const costTicks = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5].filter(
    (v) => Math.log10(v) >= cMin && Math.log10(v) <= cMax,
  );
  const timeTicks = [120, 300, 600, 1200, 1800, 3000].filter(
    (v) => Math.log10(v) >= tMin && Math.log10(v) <= tMax,
  );
  const scoreTicks = [10, 20, 30, 40, 50, 60].filter((v) => v >= sMin && v <= sMax);

  const placeSplitLabels = (side: "cost" | "time", bx0: number, bx1: number) => {
    const placed: [number, number, number, number][] = [];
    const results = new Map<string, { lx: number; ly: number; anchor: "middle" | "start" | "end" }>();
    const visible = nodes.filter(
      (n) =>
        !((highlight !== null && n.channel !== highlight) || (hitSet !== null && !hitSet.has(n.key))),
    );
    for (const n of visible) {
      const isFront = side === "cost" ? n.isCostFrontier : n.isTimeFrontier;
      const r = isFront || n.isDualWinner ? 13 : 10;
      const cx = side === "cost" ? mapCostX(n.cost) : mapTimeX(n.time);
      const cy = mapScoreY(n.score);
      placed.push([cx - r, cy - r, cx + r, cy + r]);
    }
    const targets = visible
      .filter((n) => {
        const isFront = side === "cost" ? n.isCostFrontier : n.isTimeFrontier;
        return isFront || n.isDualWinner || hoverKey === n.key;
      })
      .sort((a, b) => (b.isDualWinner ? 1 : 0) - (a.isDualWinner ? 1 : 0) || b.score - a.score);

    for (const n of targets) {
      const cx = side === "cost" ? mapCostX(n.cost) : mapTimeX(n.time);
      const cy = mapScoreY(n.score);
      const tFrac = (Math.sqrt(n.tokens) - tokMin) / Math.max(1e-6, tokMax - tokMin);
      const tokRadius = 11 + tFrac * 15;
      const w = n.family.length * 6.6 + 12;
      const h = 15;
      const rOff = tokRadius + 8;
      const candidates: [number, number, "middle" | "start" | "end"][] = [
        [0, -rOff, "middle"],
        [0, rOff + 10, "middle"],
        [rOff, 3, "start"],
        [-rOff, 3, "end"],
        [rOff, -rOff * 0.75, "start"],
        [-rOff, -rOff * 0.75, "end"],
        [rOff, rOff * 0.85 + 6, "start"],
        [-rOff, rOff * 0.85 + 6, "end"],
        [0, -rOff - 18, "middle"],
        [0, rOff + 26, "middle"],
      ];
      let chosen: { lx: number; ly: number; anchor: "middle" | "start" | "end" } | null = null;
      for (const [dx, dy, anchor] of candidates) {
        const lx = cx + dx;
        const ly = cy + dy;
        const x0 = anchor === "middle" ? lx - w / 2 : anchor === "start" ? lx : lx - w;
        const x1 = x0 + w;
        const y0 = ly - h + 3;
        const y1 = ly + 3;
        if (x0 < bx0 + 4 || x1 > bx1 - 4 || y0 < padT + 4 || y1 > padT + plotH - 4) continue;
        const hit = placed.some((b) => !(x1 < b[0] || x0 > b[2] || y1 < b[1] || y0 > b[3]));
        if (!hit) {
          placed.push([x0 - 1, y0 - 1, x1 + 1, y1 + 1]);
          chosen = { lx, ly, anchor };
          break;
        }
      }
      if (!chosen) {
        const lx = Math.max(bx0 + w / 2 + 4, Math.min(bx1 - w / 2 - 4, cx));
        const ly = Math.max(padT + 16, Math.min(padT + plotH - 6, cy - rOff));
        chosen = { lx, ly, anchor: "middle" };
      }
      results.set(n.key, chosen);
    }
    return results;
  };
  const splitLabelsCost = placeSplitLabels("cost", leftX0, leftX1);
  const splitLabelsTime = placeSplitLabels("time", rightX0, rightX1);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block", userSelect: "none", background: bg }}
    >
      {/* Left & Right Panel Backgrounds */}
      <rect
        x={leftX0}
        y={padT}
        width={panelW}
        height={plotH}
        fill={panelBg}
        stroke={gridMajor}
        rx={6}
      />
      <rect
        x={rightX0}
        y={padT}
        width={panelW}
        height={plotH}
        fill={panelBg}
        stroke={gridMajor}
        rx={6}
      />

      {/* Panel Headers */}
      <text x={leftX0 + 12} y={padT - 11} fontSize={13} fontWeight={700} fill="#F5B301">
        {zh
          ? "左视场：每任务成本 × 智力（越右越便宜 →）"
          : "Left: Cost per Task × Score (Cheaper →)"}
      </text>
      <text x={rightX0 + 12} y={padT - 11} fontSize={13} fontWeight={700} fill="#31C6F0">
        {zh
          ? "右视场：任务完成耗时 × 智力（越右越快 →）"
          : "Right: Task Completion Time × Score (Faster →)"}
      </text>
      <text
        x={(leftX1 + rightX0) / 2}
        y={padT - 11}
        fontSize={11.5}
        fontWeight={600}
        fill={text}
        textAnchor="middle"
      >
        {zh ? "共轭投影连线" : "Conjugate Link"}
      </text>

      {/* Grid Lines & Tick Labels */}
      {scoreTicks.map((s) => {
        const y = mapScoreY(s);
        return (
          <g key={`s-${s}`}>
            <line x1={leftX0} x2={leftX1} y1={y} y2={y} stroke={grid} />
            <line x1={rightX0} x2={rightX1} y1={y} y2={y} stroke={grid} />
            <text
              x={leftX0 - 8}
              y={y}
              fontSize={11}
              fill={text}
              textAnchor="end"
              dominantBaseline="central"
            >
              {s}
            </text>
          </g>
        );
      })}

      {costTicks.map((c) => {
        const x = mapCostX(c);
        return (
          <g key={`c-${c}`}>
            <line x1={x} x2={x} y1={padT} y2={padT + plotH} stroke={grid} />
            <text x={x} y={padT + plotH + 18} fontSize={11} fill={text} textAnchor="middle">
              {formatTickPrice(c)}
            </text>
          </g>
        );
      })}

      {timeTicks.map((t) => {
        const x = mapTimeX(t);
        return (
          <g key={`t-${t}`}>
            <line x1={x} x2={x} y1={padT} y2={padT + plotH} stroke={grid} />
            <text x={x} y={padT + plotH + 18} fontSize={11} fill={text} textAnchor="middle">
              {formatTickTime(t)}
            </text>
          </g>
        );
      })}

      {/* Pareto Frontier Polylines */}
      {costFrontier.length >= 2 && (
        <polyline
          fill="none"
          stroke="#F5B301"
          strokeWidth={2.4}
          points={costFrontier
            .map((n) => `${mapCostX(n.cost).toFixed(1)},${mapScoreY(n.score).toFixed(1)}`)
            .join(" ")}
        />
      )}
      {timeFrontier.length >= 2 && (
        <polyline
          fill="none"
          stroke="#31C6F0"
          strokeWidth={2.4}
          points={timeFrontier
            .map((n) => `${mapTimeX(n.time).toFixed(1)},${mapScoreY(n.score).toFixed(1)}`)
            .join(" ")}
        />
      )}

      {/* Center Spine Conjugate Bridges */}
      {nodes.map((n) => {
        const y = mapScoreY(n.score);
        const isHov = hoverKey === n.key;
        const active = n.isDualWinner || isHov;
        return (
          <g key={`bridge-${n.key}`}>
            <path
              d={`M ${leftX1} ${y.toFixed(1)} C ${leftX1 + spineW * 0.35} ${y.toFixed(1)}, ${rightX0 - spineW * 0.35} ${y.toFixed(1)}, ${rightX0} ${y.toFixed(1)}`}
              fill="none"
              stroke={isHov ? "#FFFFFF" : n.isDualWinner ? "#F5B301" : n.hexColor}
              strokeWidth={isHov ? 2.6 : n.isDualWinner ? 2.0 : 0.8}
              strokeOpacity={isHov ? 0.98 : n.isDualWinner ? 0.82 : 0.22}
            />
            {active && (
              <text
                x={(leftX1 + rightX0) / 2}
                y={y - 5}
                fontSize={10}
                fontWeight={700}
                fill={isHov ? ink : "#F5B301"}
                textAnchor="middle"
              >
                {n.family}
              </text>
            )}
          </g>
        );
      })}

      {/* Render 4D Badges on Both Panels (Token Halo + Speed Arc + Provider SVG Logo) */}
      {(["cost", "time"] as const).map((side) =>
        nodes.map((n) => {
          const cx = side === "cost" ? mapCostX(n.cost) : mapTimeX(n.time);
          const cy = mapScoreY(n.score);
          const tFrac =
            (Math.sqrt(n.tokens) - tokMin) / Math.max(1e-6, tokMax - tokMin);
          const tokRadius = 11 + tFrac * 15;
          const speedFrac =
            (n.tps - tpsMin) / Math.max(1e-6, tpsMax - tpsMin);
          const sweep = 35 + speedFrac * 315;
          const isFront = side === "cost" ? n.isCostFrontier : n.isTimeFrontier;
          const isHov = hoverKey === n.key;
          const muted =
            (highlight !== null && n.channel !== highlight) ||
            (hitSet !== null && !hitSet.has(n.key));
          const logo =
            providerLogoUrl(n.maker, dark ? "dark" : "light") ??
            providerLogoUrl(n.channel, dark ? "dark" : "light");

          return (
            <g
              key={`${side}-${n.key}`}
              transform={`translate(${cx.toFixed(1)} ${cy.toFixed(1)})`}
              opacity={muted ? 0.25 : 1}
              style={{ cursor: "pointer" }}
              onMouseEnter={() => onHover(n.key)}
              onMouseLeave={() => onHover(null)}
              onClick={() => onSelect(n.rows)}
            >
              {/* 1. Token Consumption Outer Halo */}
              <circle
                r={tokRadius}
                fill={n.hexColor}
                fillOpacity={isHov ? 0.28 : 0.14}
                stroke={n.hexColor}
                strokeOpacity={0.45}
                strokeWidth={1}
                strokeDasharray="2 2"
              />
              {/* 2. Output Throughput (tok/s) Precision Arc */}
              <path
                d={describeArc(0, 0, tokRadius + 3.2, 0, sweep)}
                fill="none"
                stroke={n.isDualWinner ? "#F5B301" : n.hexColor}
                strokeWidth={2.4}
                strokeLinecap="round"
                opacity={0.92}
              />
              {/* 3. Provider Logo Badge Core */}
              <circle
                r={isFront || n.isDualWinner ? 10.5 : 8.5}
                fill={dark ? "#161E2E" : "#FFFFFF"}
                stroke={
                  n.isDualWinner
                    ? "#F5B301"
                    : isFront
                      ? side === "cost"
                        ? "#F5B301"
                        : "#31C6F0"
                      : n.hexColor
                }
                strokeWidth={n.isDualWinner ? 2.4 : isFront ? 1.8 : 1.2}
              />
              {logo ? (
                <image
                  href={logo}
                  x={-7}
                  y={-7}
                  width={14}
                  height={14}
                  preserveAspectRatio="xMidYMid meet"
                />
              ) : (
                <text
                  fontSize={7.5}
                  fontWeight={700}
                  fill={ink}
                  textAnchor="middle"
                  dominantBaseline="central"
                >
                  {n.vendor.slice(0, 2)}
                </text>
              )}
              {(() => {
                const lbl = (side === "cost" ? splitLabelsCost : splitLabelsTime).get(n.key);
                if (!lbl) return null;
                return (
                  <text
                    x={lbl.lx - cx}
                    y={lbl.ly - cy}
                    fontSize={10.5}
                    fontWeight={n.isDualWinner ? 700 : 600}
                    fill={n.isDualWinner ? "#F5B301" : ink}
                    textAnchor={lbl.anchor}
                    style={{
                      paintOrder: "stroke",
                      stroke: panelBg,
                      strokeWidth: 3.2,
                    }}
                  >
                    {n.family}
                  </text>
                );
              })()}
            </g>
          );
        }),
      )}

      {/* Bottom 4D Encoding Legend */}
      <text x={leftX0} y={height - 12} fontSize={11.5} fill={text}>
        {zh
          ? "四维编码图例：圆心徽章 = 模型厂商 Logo · 虚线光环面积 ∝ 每任务消耗 Token 数 (50k~285k) · 外圈实线圆弧开角 ∝ 生成速度 tok/s (40~891) · 金冠双连线 = 成本与耗时双料帕累托最优"
          : "4D Encoding: Center badge = Vendor Logo · Dashed halo area ∝ Tokens/task · Outer arc sweep ∝ Speed (tok/s) · Gold bridge = Dual Cost & Time Pareto Winner"}
      </text>
    </svg>
  );
}
