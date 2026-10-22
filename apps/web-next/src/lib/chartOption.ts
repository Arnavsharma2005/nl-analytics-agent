/**
 * Turn the API's chart spec and result rows into an ECharts option.
 *
 * kpi and table specs are not drawn as charts (the answer shows them as figures or a table), so they get null.
 * Colours come from the page's CSS variables, so a chart follows the light or dark theme.
 */
import type { Cell, ChartSpec } from "../api/types";
import { formatDate, formatValue, humanize } from "./format";

export interface Palette {
  series: string[];
  text: string;
  muted: string;
  rule: string;
  surface: string;
}

export function readPalette(el: Element = document.documentElement): Palette {
  const css = getComputedStyle(el);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    series: v("--series").split(",").map((c) => c.trim()).filter(Boolean),
    text: v("--text"),
    muted: v("--muted"),
    rule: v("--rule"),
    surface: v("--surface"),
  };
}

export const drawable = (spec: ChartSpec | undefined | null, rows: Cell[][]) =>
  !!spec && !!spec.x && spec.y.length > 0 && rows.length > 0 && ["bar", "line", "pie", "scatter"].includes(spec.type);

const label = (v: Cell, col: string) => (typeof v === "string" ? formatDate(v, col) : v === null ? "–" : String(v));

/** Long rows (x, series, y) to one list of values per series, in the order the x values first appear. */
export function pivot(columns: string[], rows: Cell[][], x: string, series: string, y: string) {
  const [xi, si, yi] = [x, series, y].map((c) => columns.indexOf(c));
  const xs: string[] = [];
  const names: string[] = [];
  const cells = new Map<string, Cell>();
  for (const r of rows) {
    const xv = label(r[xi], x);
    const sv = String(r[si]);
    if (!xs.includes(xv)) xs.push(xv);
    if (!names.includes(sv)) names.push(sv);
    cells.set(`${xv}\u0000${sv}`, r[yi]);
  }
  return { xs, series: names.map((name) => ({ name, data: xs.map((xv) => cells.get(`${xv}\u0000${name}`) ?? null) })) };
}

export function chartOption(spec: ChartSpec, columns: string[], rows: Cell[][], p: Palette): Record<string, unknown> | null {
  if (!drawable(spec, rows)) return null;
  const x = spec.x!;
  const xi = columns.indexOf(x);
  const y = spec.y.filter((c) => columns.includes(c));
  if (xi < 0 || !y.length) return null;
  const col = (c: string) => rows.map((r) => r[columns.indexOf(c)]);
  const yFormat = (v: unknown) => formatValue(v, y[0], true);

  const base = {
    color: p.series,
    backgroundColor: "transparent",
    textStyle: { fontFamily: "IBM Plex Sans, system-ui, sans-serif", color: p.text },
    aria: { enabled: true, decal: { show: false } },
    animationDuration: 300,
  };
  const tooltip = { backgroundColor: p.surface, borderColor: p.rule, textStyle: { color: p.text } };
  const axis = {
    axisLine: { lineStyle: { color: p.rule } },
    axisTick: { show: false },
    axisLabel: { color: p.muted },
    splitLine: { lineStyle: { color: p.rule } },
  };

  if (spec.type === "pie") {
    return {
      ...base,
      tooltip: { ...tooltip, trigger: "item", valueFormatter: (v: unknown) => formatValue(v, y[0]) },
      series: [{
        type: "pie",
        radius: ["45%", "75%"],
        itemStyle: { borderColor: p.surface, borderWidth: 2 },
        label: { color: p.text, formatter: "{b} {d}%" },
        data: rows.map((r) => ({ name: label(r[xi], x), value: r[columns.indexOf(y[0])] })),
      }],
    };
  }

  if (spec.type === "scatter") {
    return {
      ...base,
      grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
      tooltip: { ...tooltip, trigger: "item" },
      xAxis: { ...axis, type: "value", name: humanize(x), nameLocation: "middle", nameGap: 28, axisLabel: { ...axis.axisLabel, formatter: (v: number) => formatValue(v, x, true) } },
      yAxis: { ...axis, type: "value", name: humanize(y[0]), axisLabel: { ...axis.axisLabel, formatter: yFormat } },
      series: [{ type: "scatter", symbolSize: 9, data: rows.map((r) => [r[xi], r[columns.indexOf(y[0])]]) }],
    };
  }

  let categories: string[];
  let series: { name: string; data: Cell[] }[];
  if (spec.series && columns.includes(spec.series)) {
    ({ xs: categories, series } = pivot(columns, rows, x, spec.series, y[0]));
  } else {
    categories = col(x).map((v) => label(v, x));
    series = y.map((c) => ({ name: humanize(c), data: col(c) }));
  }

  const bar = spec.type === "bar";
  // Many long category names read better down the side than squeezed along the bottom.
  const horizontal = bar && series.length === 1 && categories.length > 6 && categories.some((c) => c.length > 10);
  const categoryAxis = { ...axis, type: "category", data: categories, splitLine: { show: false },
    axisLabel: { ...axis.axisLabel, hideOverlap: true, ...(horizontal ? { width: 140, overflow: "truncate" } : {}) } };
  const valueAxis = { ...axis, type: "value", axisLabel: { ...axis.axisLabel, formatter: yFormat } };

  return {
    ...base,
    grid: { left: 8, right: 16, top: series.length > 1 ? 40 : 16, bottom: 8, containLabel: true },
    legend: series.length > 1 ? { top: 0, left: 0, textStyle: { color: p.muted }, icon: "roundRect" } : undefined,
    tooltip: { ...tooltip, trigger: "axis", axisPointer: { type: bar ? "shadow" : "line" },
      valueFormatter: (v: unknown) => formatValue(v, y[0]) },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
    series: series.map((s) => bar
      ? { ...s, type: "bar", barMaxWidth: 36, itemStyle: { borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] } }
      : { ...s, type: "line", smooth: false, showSymbol: categories.length <= 24, symbolSize: 6, lineStyle: { width: 2 } }),
  };
}
