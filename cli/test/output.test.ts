import assert from "node:assert/strict";
import test from "node:test";
import { parse } from "csv-parse/sync";
import { renderResult } from "../src/output.js";
import type { Benchmark, DatasetContext, Metadata, PublicPoint, Result } from "../src/types.js";

const point: PublicPoint = {
  id: "droid_max::claude-opus-5.5", plan_id: "droid_max", plan: "Droid Max",
  plan_display: "Droid Max", model: "claude-opus-5.5", model_display: "Claude Opus 5.5",
  vendor: "Anthropic", company: "Anthropic", company_display: "Anthropic", channel: "Factory",
  label: "Droid Max × Claude Opus 5.5", billing: "subscription", confidence: "medium",
  price_usd: 200, original_price: 200, currency: "USD", monthly_yi: 50.52,
  monthly_tokens: 5052000000, real_usd_per_mtok: 0.039588282,
  list_blended_usd_per_mtok: 0.419, workload: "anthropic", data_date: "2026-09-29",
  data_date_kind: "sample", unmetered: false, promo_until: null,
  source: 'Original source: "quoted"\n第二行, evidence',
  note: "", decision_note: "Adopted allowance, with limitations",
  evidence: [{ label: "sample.json", url: "/data/evidence/sample.json" }],
};
const benchmark: Benchmark = {
  point_id: point.id, configuration_id: "aa_intelligence_index:6df46e4e9119d1e1",
  board: "aa_intelligence_index", variant: "Claude Opus 5.5 (max with fallback)",
  score: 57.6224, reasoning_effort: "max", agent_harness: null, service_mode: null,
  score_low: null, score_high: null, source: "https://example.com/benchmark",
  mapping_kind: "model_configuration_reference", mapping_confidence: "medium",
  mapping_note: "Exact served-model reference; quota-measurement effort is unverified.",
  quota_effort_matched: null, score_is_estimated: false, score_is_self_reported: false,
  best_tie_count: 1,
};
const meta: Metadata = {
  command: "price", resource: null, snapshot: "2026-10-01", source: "bundled",
  sourcePath: null, view: "price", board: null, boardSnapshot: null,
  total: 1, returned: 1, truncated: false, warnings: [],
};
const result = (rows: unknown[], changes: Partial<Metadata> = {}): Result => ({
  schemaVersion: 1, meta: { ...meta, ...changes }, rows,
});
const queryResult = result([{ rank: 1, point, benchmark: null }]);
const context: DatasetContext = {
  source: "bundled", sourcePath: null,
  data: {
    version: 1, generatedAt: meta.snapshot, points: [point], configurations: [], mappings: [benchmark],
    boards: { aa_intelligence_index: { name: "Intelligence Board", metric: "Intelligence Index", url: "https://example.com", snapshot: "2026-09-22" } },
    conventions: {
      usdPerCny: 6.7787, monthWeeks: 4,
      exchangeRate: { date: "2026-09-04", source: "Exchange source", labelEn: "", labelZh: "" },
      standardTokenMix: { cache: 0.97, input: 0.025, output: 0.005 },
      lowCacheTokenMix: { cache: 0.85, input: 0.145, output: 0.005 },
      anthropicTokenMix: { cache: 0.97, cacheWrite: 0.025, output: 0.005 },
    },
  },
};

function csvRecords(output: string): Record<string, string>[] {
  return parse(output, { columns: true });
}

test("JSON is an unmodified parseable schema object, with all warnings in metadata", () => {
  const r = result([{ rank: 1, point, benchmark }], {
    warnings: [{ code: "test", message: "Quota effort is unverified.", point_id: point.id }],
  });
  const output = renderResult(r, "json", context);
  assert.deepEqual(JSON.parse(output.stdout), r);
  assert.equal(output.stderr, "");
  assert.equal(JSON.parse(output.stdout).rows[0].point.real_usd_per_mtok, 0.039588282);
});

test("CSV retains quoted multiline sources, raw precision, nulls, booleans and JSON cells", () => {
  const output = renderResult(queryResult, "csv");
  const [row] = csvRecords(output.stdout);
  assert.equal(Object.keys(row!).length, 26);
  assert.equal(row!.source, point.source);
  assert.equal(row!.real_usd_per_mtok, "0.039588282");
  assert.equal(row!.rank, "1");
  assert.equal(row!.board, "");
  assert.equal(row!.benchmark_json, "");
  assert.equal(row!.unmetered, "false");
  assert.deepEqual(JSON.parse(row!.evidence_json!), point.evidence);
  assert.match(output.stderr, /Snapshot: 2026-10-01/);
  assert.match(output.stderr, /exported_rows: 1/);
});

test("CSV spreadsheet protection applies to source text without rewriting identifiers or numbers", () => {
  const unsafe: PublicPoint = {
    ...point, id: "=point::+model", model: "+model", plan_id: "-plan", price_usd: 0,
    source: '=HYPERLINK("https://example.com")', decision_note: " @SUM(1,2)",
    plan_display: "+unsafe plan", monthly_tokens: null, monthly_yi: null,
  };
  const [row] = csvRecords(renderResult(result([{ point: unsafe, benchmark: null }]), "csv").stdout);
  assert.equal(row!.source, `'${unsafe.source}`);
  assert.equal(row!.decision_note, `'${unsafe.decision_note}`);
  assert.equal(row!.plan_display, "'+unsafe plan");
  assert.equal(row!.point_id, "=point::+model");
  assert.equal(row!.model, "+model");
  assert.equal(row!.plan_id, "-plan");
  assert.equal(row!.price_usd, "0");
  assert.equal(row!.monthly_tokens, "");
});

test("table distinguishes Model, Company, Channel and Plan while rankings never include scores", () => {
  const output = renderResult(result([{ rank: 1, point, benchmark }], {
    board: benchmark.board, boardSnapshot: "2026-09-22",
  }), "table", context);
  for (const field of ["Rank", "Model", "Company", "Channel", "Plan", "Point ID"]) assert.ok(output.stdout.includes(field));
  assert.ok(output.stdout.includes("Anthropic"));
  assert.ok(output.stdout.includes("Factory"));
  assert.ok(output.stdout.includes("Droid Max"));
  assert.ok(output.stdout.includes(point.id));
  assert.ok(output.stdout.includes("$0.03959"));
  assert.ok(output.stdout.includes("5.052 B"));
  assert.ok(!output.stdout.includes(String(benchmark.score)));
});

test("show CSV expands configurations without changing detail metadata and retains mapping evidence", () => {
  const second = { ...benchmark, configuration_id: "aa_intelligence_index:other", score: 55, reasoning_effort: "high" };
  const r = result([{ point, benchmarks: [benchmark, second] }], { command: "show", view: null });
  const output = renderResult(r, "csv", context);
  const rows = csvRecords(output.stdout);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((row) => row.configuration_id), [benchmark.configuration_id, second.configuration_id]);
  assert.equal(JSON.parse(rows[0]!.benchmark_json!).mapping_note, benchmark.mapping_note);
  assert.match(output.stderr, /Total: 1 \| Returned: 1/);
  assert.match(output.stderr, /exported_rows: 2/);
  assert.deepEqual(JSON.parse(renderResult(r, "json").stdout).rows[0].benchmarks, [benchmark, second]);
  const tableOutput = renderResult(r, "table", context).stdout;
  assert.ok(tableOutput.includes(point.source));
  assert.ok(tableOutput.includes(point.decision_note));
  assert.ok(tableOutput.includes(benchmark.configuration_id));
  assert.ok(tableOutput.includes(second.configuration_id));
  assert.ok(tableOutput.includes("2026-09-22"));
  assert.ok(tableOutput.includes("Intelligence Board"));
});

test("show without mappings exports one price row; empty queries preserve format contracts", () => {
  const show = result([{ point, benchmarks: [] }], { command: "show", view: null });
  assert.equal(csvRecords(renderResult(show, "csv").stdout).length, 1);
  assert.match(renderResult(show, "table").stdout, /No mapped benchmark configurations/);
  const empty = result([], { total: 0, returned: 0 });
  assert.equal(renderResult(empty, "table").stdout, "No matching results.\n");
  assert.deepEqual(JSON.parse(renderResult(empty, "json").stdout).rows, []);
  const csv = renderResult(empty, "csv");
  assert.equal(csvRecords(csv.stdout).length, 0);
  assert.equal(csv.stdout.trim().split(",").length, 26);
  assert.match(csv.stderr, /exported_rows: 0/);
});

test("API and unknown subscription allowances have distinct table displays; promos retain actual monthly fees", () => {
  const api = { ...point, billing: "metered", price_usd: null, monthly_tokens: null, monthly_yi: null };
  const unknown = { ...point, monthly_tokens: null, monthly_yi: null };
  assert.match(renderResult(result([{ point: api, benchmark: null }]), "table").stdout, /N\/A/);
  assert.match(renderResult(result([{ point: unknown, benchmark: null }]), "table").stdout, /Unknown/);
  const promo = { ...unknown, unmetered: true, promo_until: "2026-12-31", real_usd_per_mtok: 0, price_usd: 10 };
  const promoOutput = renderResult(result([{ point: promo, benchmark: null }]), "table").stdout;
  // Narrow columns may wrap, but every part of the qualification remains present.
  assert.ok(promoOutput.includes("2026-12-31"));
  assert.ok(promoOutput.includes("unmetered"));
  assert.ok(promoOutput.includes("10"));
});

test("custom dataset source path remains visible in diagnostics and info CSV has stable columns", () => {
  const r = result([{ point, benchmark: null }], { source: "file", sourcePath: "/tmp/custom data.json" });
  assert.match(renderResult(r, "csv").stderr, /Source: file: \/tmp\/custom data.json/);
  const info = result([{
    snapshot: "2026-10-01", source: "file", source_path: "/tmp/custom data.json", datasetVersion: 1,
    counts: { points: 1, models: 1, companies: 1, channels: 1, plans: 1, boards: 1, configurations: 1, mappings: 1 },
    conventions: { ...context.data.conventions, subscriptionPriceAssumption: "Full use", allowancesAcrossModels: "Alternative use", measuredWorkload: "Raw sample tokens" },
  }], { command: "info", view: null });
  const [row] = csvRecords(renderResult(info, "csv").stdout);
  assert.deepEqual(Object.keys(row!), ["snapshot", "source", "source_path", "counts_json", "conventions_json"]);
  assert.equal(row!.source_path, "/tmp/custom data.json");
  assert.equal(JSON.parse(row!.counts_json!).points, 1);
  assert.ok(renderResult(info, "table").stdout.includes("1 USD = 6.7787 CNY"));
  assert.ok(!renderResult(info, "table").stdout.includes("1 USD = 0.1475"));
});

test("ranking and query summaries explain the active order, fee band and configuration selection", () => {
  const ranking = renderResult(queryResult, "table");
  assert.match(ranking.stderr, /Order: USD\/MTok ascending; ties by Point ID/);
  const allowance = renderResult(result([{ rank: 1, point, benchmark: null }], { command: "allowance", view: "allowance" }), "csv", context, {
    feeBand: "0-30", sort: "allowance:desc", config: "best",
  });
  assert.match(allowance.stderr, /Order: monthly tokens descending/);
  assert.match(allowance.stderr, /Fee band: 0-30/);
  const query = renderResult(result([{ point, benchmark }], {
    command: "query", view: "table", board: benchmark.board, boardSnapshot: "2026-09-22",
  }), "table", context, { feeBand: "all", sort: "score", config: "all" });
  assert.match(query.stderr, /Config: all/);
  assert.match(query.stderr, /Order: score:asc/);
  assert.equal(renderResult(queryResult, "json", context, { feeBand: "all", sort: "price:asc", config: "best" }).stderr, "");
});

test("narrow terminals retain each complete price-point identifier on a continuation and redirected output stays stable", () => {
  const tty = Object.getOwnPropertyDescriptor(process.stdout, "isTTY");
  const columns = Object.getOwnPropertyDescriptor(process.stdout, "columns");
  try {
    Object.defineProperty(process.stdout, "isTTY", { configurable: true, value: true });
    Object.defineProperty(process.stdout, "columns", { configurable: true, value: 80 });
    const narrow = renderResult(queryResult, "table").stdout;
    assert.ok(narrow.includes(`Point ID: ${point.id}`));
    assert.ok(!narrow.includes("…"));
    assert.ok(narrow.split("\n").every((line) => line.length <= 80));
    Object.defineProperty(process.stdout, "isTTY", { configurable: true, value: false });
    const redirected = renderResult(queryResult, "table").stdout;
    Object.defineProperty(process.stdout, "columns", { configurable: true, value: 260 });
    assert.equal(renderResult(queryResult, "table").stdout, redirected);
  } finally {
    if (tty) Object.defineProperty(process.stdout, "isTTY", tty); else delete (process.stdout as unknown as Record<string, unknown>).isTTY;
    if (columns) Object.defineProperty(process.stdout, "columns", columns); else delete (process.stdout as unknown as Record<string, unknown>).columns;
  }
});
