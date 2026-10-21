import {
  Bar, BarChart, CartesianGrid, Cell as PieCell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import { formatDate, formatValue, humanize } from "../format";
import type { Cell, ChartSpec } from "../types";
import DataTable from "./DataTable";

const COLORS = ["#2b59c3", "#e07a1f", "#2a9d8f", "#c2417a", "#7b61c9", "#8a9a2b", "#d1495b", "#4a8fd4"];

interface Props {
  spec: ChartSpec;
  columns: string[];
  rows: Cell[][];
}

type Datum = Record<string, Cell>;

function toObjects(columns: string[], rows: Cell[][]): Datum[] {
  return rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
}

/** Long format (x, series, y) -> wide format with one key per series value. */
function pivot(data: Datum[], x: string, series: string, y: string): { data: Datum[]; keys: string[] } {
  const keys: string[] = [];
  const byX = new Map<string, Datum>();
  for (const d of data) {
    const key = String(d[series]);
    if (!keys.includes(key)) keys.push(key);
    const xv = String(d[x]);
    if (!byX.has(xv)) byX.set(xv, { [x]: d[x] });
    byX.get(xv)![key] = d[y];
  }
  return { data: [...byX.values()], keys };
}

export default function ChartView({ spec, columns, rows }: Props) {
  if (spec.type === "table" || !rows.length) return <DataTable columns={columns} rows={rows} />;

  const data = toObjects(columns, rows);

  if (spec.type === "kpi") {
    return (
      <div className="kpis">
        {spec.y.map((col) => (
          <div className="kpi" key={col}>
            <div className="kpi-value">{formatValue(data[0][col], col, true)}</div>
            <div className="kpi-label">{humanize(col)}</div>
          </div>
        ))}
      </div>
    );
  }

  const x = spec.x!;
  const yCol = spec.y[0];
  const tick = (v: unknown) => (typeof v === "string" ? formatDate(v, x) : String(v));
  const yTick = (v: number) => formatValue(v, yCol, true);
  const tip = (v: unknown, name: unknown) => {
    const col = spec.y.includes(String(name)) ? String(name) : yCol;
    return [formatValue(v, col), spec.y.includes(String(name)) ? humanize(String(name)) : String(name)] as [string, string];
  };
  const axisProps = { stroke: "var(--muted)", fontSize: 12, tickLine: false };

  let chartData = data;
  let keys = spec.y;
  if (spec.series) ({ data: chartData, keys } = pivot(data, x, spec.series, yCol));
  const legend = keys.length > 1 ? <Legend formatter={(v: string) => (spec.y.includes(v) ? humanize(v) : v)} /> : null;

  let chart: React.ReactElement;
  if (spec.type === "line") {
    chart = (
      <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={x} tickFormatter={tick} {...axisProps} />
        <YAxis tickFormatter={yTick} width={80} {...axisProps} />
        <Tooltip formatter={tip} labelFormatter={tick} />
        {legend}
        {keys.map((k, i) => (
          <Line key={k} dataKey={k} isAnimationActive={false} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={chartData.length <= 24} type="monotone" />
        ))}
      </LineChart>
    );
  } else if (spec.type === "bar") {
    const horizontal = !spec.series && chartData.length > 6 && chartData.some((d) => String(d[x]).length > 10);
    chart = horizontal ? (
      <BarChart data={chartData} layout="vertical" margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid stroke="var(--grid)" horizontal={false} />
        <XAxis type="number" tickFormatter={yTick} {...axisProps} />
        <YAxis type="category" dataKey={x} width={150} tickFormatter={tick} {...axisProps} />
        <Tooltip formatter={tip} labelFormatter={tick} />
        {legend}
        {keys.map((k, i) => <Bar key={k} dataKey={k} isAnimationActive={false} fill={COLORS[i % COLORS.length]} radius={[0, 4, 4, 0]} />)}
      </BarChart>
    ) : (
      <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={x} tickFormatter={tick} interval={0} {...axisProps} />
        <YAxis tickFormatter={yTick} width={80} {...axisProps} />
        <Tooltip formatter={tip} labelFormatter={tick} />
        {legend}
        {keys.map((k, i) => <Bar key={k} dataKey={k} isAnimationActive={false} fill={COLORS[i % COLORS.length]} radius={[4, 4, 0, 0]} />)}
      </BarChart>
    );
  } else if (spec.type === "pie") {
    chart = (
      <PieChart>
        <Pie data={data} isAnimationActive={false} dataKey={yCol} nameKey={x} innerRadius="45%" outerRadius="80%" paddingAngle={1}
          label={({ name, percent }: { name?: string; percent?: number }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}>
          {data.map((_, i) => <PieCell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip formatter={tip} />
      </PieChart>
    );
  } else {
    chart = (
      <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid stroke="var(--grid)" />
        <XAxis dataKey={x} name={humanize(x)} type="number" tickFormatter={(v: number) => formatValue(v, x, true)} {...axisProps} />
        <YAxis dataKey={yCol} name={humanize(yCol)} tickFormatter={yTick} width={80} {...axisProps} />
        <Tooltip formatter={tip} />
        <Scatter data={data} isAnimationActive={false} fill={COLORS[0]} />
      </ScatterChart>
    );
  }

  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height={340}>{chart}</ResponsiveContainer>
    </div>
  );
}
