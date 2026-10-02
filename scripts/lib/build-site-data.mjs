import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

// Both the website and the offline CLI publish this same validated snapshot.
// Inject the CSV parser so this shared adapter has no package dependencies.
export async function buildSiteData({ repo, parseCsv }) {
  const read = async (p) =>
    JSON.parse(await readFile(path.join(repo, p), "utf8"));
  const [
    points,
    configurations,
    mappings,
    conventions,
    adoptedText,
    evidenceFiles,
    channelColors,
  ] = await Promise.all([
    read("derived/points.json"),
    read("derived/benchmark-configurations.json"),
    read("derived/benchmark-points.json"),
    read("data/conventions.json"),
    readFile(path.join(repo, "data/adopted.csv"), "utf8"),
    readdir(path.join(repo, "data/research")),
    read("config/channel-colors.json"),
  ]);
  const adopted = new Map(
    parseCsv(adoptedText, { columns: true, skip_empty_lines: true, bom: true }).map(
      (p) => [`${p.plan_id}::${p.served_model}`, p],
    ),
  );
  const configById = new Map(configurations.map((c) => [c.configuration_id, c]));
  const pointIds = new Set(points.points.map((p) => p.id));
  if (
    pointIds.size !== points.points.length ||
    adopted.size !== points.points.length
  )
    throw new Error("Duplicate points or adopted/points count mismatch");
  for (const m of mappings)
    if (!pointIds.has(m.point_id) || !configById.has(m.configuration_id))
      throw new Error("Orphan benchmark mapping");
  const evidenceToCopy = new Set();
  // id 前缀 → 渠道与 Python 侧共用 config/channel-colors.json 的 channels 数组。
  const channelPrefixes = channelColors.channels;
  const channel = (p) =>
    channelPrefixes.find(([prefix]) => p.id.startsWith(prefix))?.[1] || p.vendor;
  const data = {
    version: 1,
    generatedAt: points.generatedAt,
    boards: points.boards,
    conventions,
    points: points.points.map((p) => {
      const a = adopted.get(p.id);
      if (
        !a ||
        Number(a.real_usd_per_mtok) !== p.real_usd_per_mtok ||
        (a.price_usd === "" ? null : Number(a.price_usd)) !== p.price_usd ||
        (a.monthly_yi === "" ? null : Number(a.monthly_yi)) !== p.monthly_yi
      )
        throw new Error(`Adoption mismatch: ${p.id}`);
      const text = `${a.source} ${a.decision_note}`;
      const evidence = evidenceFiles
        .filter((f) => f.endsWith(".json") && text.includes(f))
        .map((f) => {
          evidenceToCopy.add(f);
          return { label: f, url: `/data/evidence/${f}` };
        });
      const urls = [
        ...new Set(text.match(/https?:\/\/[^\s<>"'\u3000-\u9fff；，）]+/g) || []),
      ].map((url) => ({ label: url, url: url.replace(/[;,.]+$/, "") }));
      // Score summaries are already represented losslessly by configurations + mappings.
      // Keep the web point record lean instead of repeating four boards of metadata per point.
      const base = Object.fromEntries(
        Object.entries(p).filter(([key]) => !key.includes("__")),
      );
      return {
        ...base,
        plan_id: a.plan_id,
        channel: channel(p),
        original_price: a.price === "" ? null : Number(a.price),
        currency: a.currency,
        monthly_tokens: a.monthly_tokens === "" ? null : Number(a.monthly_tokens),
        decision_note: a.decision_note,
        evidence: [...urls, ...evidence],
      };
    }),
    configurations: configurations.map(({ raw_record, ...c }) => c),
    mappings: mappings.map((m) => {
      const config = configById.get(m.configuration_id);
      return Object.fromEntries(Object.entries(m).filter(([key, value]) =>
        key === "configuration_id" || !(key in config) || config[key] !== value,
      ));
    }),
  };
  return { data, evidenceToCopy };
}
