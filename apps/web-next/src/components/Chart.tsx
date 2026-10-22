/** An ECharts chart. Loaded on demand (see LazyChart), so the first page load stays small. */
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { init, use, type ECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import type { Cell, ChartSpec } from "../api/types";
import { chartOption, readPalette } from "../lib/chartOption";
import { useDarkMode } from "../lib/hooks";

use([BarChart, LineChart, PieChart, ScatterChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export interface ChartProps {
  spec: ChartSpec;
  columns: string[];
  rows: Cell[][];
  /** Receives the chart once it is drawn (and null when it goes), e.g. to save it as a picture. */
  onReady?: (chart: ECharts | null) => void;
}

export default function Chart({ spec, columns, rows, onReady }: ChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<ECharts | null>(null);
  const dark = useDarkMode();

  useEffect(() => {
    const instance = init(el.current!, undefined, { renderer: "canvas" });
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(el.current!);
    chart.current = instance;
    onReady?.(instance);
    return () => {
      resize.disconnect();
      onReady?.(null);
      chart.current = null;
      instance.dispose();
    };
  }, [onReady]);

  useEffect(() => {
    const option = chartOption(spec, columns, rows, readPalette());
    if (chart.current && option) chart.current.setOption(option, { notMerge: true });
  }, [spec, columns, rows, dark, onReady]);

  // ECharts replaces the label with a spoken description of the chart.
  return <div ref={el} className="h-80 w-full" role="img" aria-label={spec.title} />;
}
