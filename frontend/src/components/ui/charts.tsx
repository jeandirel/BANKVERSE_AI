"use client";

/**
 * Chart wrappers (Recharts) following the platform dataviz rules: validated categorical slots,
 * thin marks, recessive grid, hover tooltips, a legend for >= 2 series, a data-table view for
 * accessibility, and never two y-axes.
 */

import { useState, type ReactNode } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  ReferenceLine,
  Cell,
} from "recharts";
import { Table2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { useL } from "@/lib/i18n";

export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)"];

export type SeriesDef = { key: string; label: string; color?: string; dashed?: boolean };

const axisTick = { fill: "var(--ink-3)", fontSize: 11.5 };

function ChartTooltip({ active, payload, label, fmt }: { active?: boolean; payload?: { name?: string; value?: number | number[]; color?: string; dataKey?: string }[]; label?: string | number; fmt?: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded border border-line bg-surface px-3 py-2 text-xs shadow-[var(--shadow-pop)]">
      <p className="text-ink-3 mb-1">{label}</p>
      {payload.filter((p) => p.value !== undefined && p.value !== null).map((p) => (
        <p key={String(p.dataKey)} className="flex items-center gap-2 text-ink">
          <span className="inline-block size-2 rounded-full" style={{ background: p.color }} />
          <span className="text-ink-2">{p.name}</span>
          <span className="num ml-auto font-medium">
            {Array.isArray(p.value) ? p.value.map((v) => (fmt ? fmt(v) : v)).join(" – ") : fmt ? fmt(p.value as number) : p.value}
          </span>
        </p>
      ))}
    </div>
  );
}

function DataView({ data, xKey, series, fmt }: { data: Record<string, unknown>[]; xKey: string; series: SeriesDef[]; fmt?: (v: number) => string }) {
  return (
    <div className="max-h-72 overflow-auto scroll-thin">
      <table className="w-full text-xs">
        <thead><tr className="border-b border-line text-ink-3"><th className="text-left py-1.5 px-2">{xKey}</th>
          {series.map((s) => <th key={s.key} className="text-right py-1.5 px-2">{s.label}</th>)}</tr></thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i} className="border-b border-line">
              <td className="py-1 px-2 text-ink-2">{String(row[xKey])}</td>
              {series.map((s) => {
                const v = row[s.key];
                return <td key={s.key} className="py-1 px-2 text-right num">{typeof v === "number" ? (fmt ? fmt(v) : v) : Array.isArray(v) ? v.join(" – ") : "—"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChartFrame({ title, children, data, xKey, series, fmt, height = 240, footer }: {
  title?: string; children: ReactNode; data: Record<string, unknown>[]; xKey: string; series: SeriesDef[];
  fmt?: (v: number) => string; height?: number; footer?: ReactNode;
}) {
  const L = useL();
  const [table, setTable] = useState(false);
  return (
    <figure className="w-full">
      <div className="flex items-center justify-between gap-2 mb-2">
        {title ? <figcaption className="text-[13px] font-medium text-ink-2">{title}</figcaption> : <span />}
        <button type="button" onClick={() => setTable((v) => !v)} aria-pressed={table}
          className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-ink-3 hover:text-ink", table && "text-ink bg-surface-2")}>
          <Table2 className="size-3.5" /> {L("Données", "Data")}
        </button>
      </div>
      {table ? <DataView data={data} xKey={xKey} series={series} fmt={fmt} /> : <div style={{ height }}>{children}</div>}
      {footer}
    </figure>
  );
}

export function TimeSeriesChart({ data, xKey, series, band, fmt, height, title, yDomain, refLine, xFmt }: {
  data: Record<string, unknown>[]; xKey: string; series: SeriesDef[]; band?: { key: string; label: string; color?: string };
  fmt?: (v: number) => string; height?: number; title?: string; yDomain?: [number | "auto", number | "auto"];
  refLine?: { x?: string; y?: number; label: string }; xFmt?: (v: string) => string;
}) {
  const all = band ? [...series, { key: band.key, label: band.label }] : series;
  return (
    <ChartFrame title={title} data={data} xKey={xKey} series={all} fmt={fmt} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey={xKey} tick={axisTick} tickLine={false} axisLine={{ stroke: "var(--line)" }} minTickGap={28} tickFormatter={xFmt} />
          <YAxis tick={axisTick} tickLine={false} axisLine={false} width={56} tickFormatter={fmt} domain={yDomain ?? ["auto", "auto"]} />
          <Tooltip content={<ChartTooltip fmt={fmt} />} cursor={{ stroke: "var(--line-strong)", strokeWidth: 1 }} />
          {band && <Area dataKey={band.key} name={band.label} stroke="none" fill={band.color ?? "var(--series-1)"} fillOpacity={0.14} isAnimationActive={false} />}
          {series.map((s, i) => (
            <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color ?? SERIES[i % SERIES.length]} strokeWidth={2}
              strokeDasharray={s.dashed ? "5 4" : undefined} dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
              isAnimationActive={false} connectNulls={false} />
          ))}
          {refLine?.x && <ReferenceLine x={refLine.x} stroke="var(--ink-3)" strokeDasharray="3 3" label={{ value: refLine.label, fill: "var(--ink-3)", fontSize: 11, position: "insideTopRight" }} />}
          {refLine?.y !== undefined && <ReferenceLine y={refLine.y} stroke="var(--ink-3)" strokeDasharray="3 3" label={{ value: refLine.label, fill: "var(--ink-3)", fontSize: 11, position: "insideTopLeft" }} />}
          {all.length > 1 && <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: "var(--ink-2)" }} />}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function BarsChart({ data, xKey, series, fmt, height, title, horizontal, colorBy }: {
  data: Record<string, unknown>[]; xKey: string; series: SeriesDef[]; fmt?: (v: number) => string; height?: number;
  title?: string; horizontal?: boolean; colorBy?: (row: Record<string, unknown>) => string;
}) {
  return (
    <ChartFrame title={title} data={data} xKey={xKey} series={series} fmt={fmt} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ top: 6, right: 16, bottom: 0, left: horizontal ? 8 : 0 }} barCategoryGap="22%">
          <CartesianGrid stroke="var(--grid)" vertical={!!horizontal} horizontal={!horizontal} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={axisTick} tickLine={false} axisLine={false} tickFormatter={fmt} />
              <YAxis type="category" dataKey={xKey} tick={axisTick} tickLine={false} axisLine={{ stroke: "var(--line)" }} width={150} />
            </>
          ) : (
            <>
              <XAxis dataKey={xKey} tick={axisTick} tickLine={false} axisLine={{ stroke: "var(--line)" }} minTickGap={8} />
              <YAxis tick={axisTick} tickLine={false} axisLine={false} width={52} tickFormatter={fmt} />
            </>
          )}
          <Tooltip content={<ChartTooltip fmt={fmt} />} cursor={{ fill: "var(--surface-2)" }} />
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color ?? SERIES[i % SERIES.length]} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
              isAnimationActive={false} maxBarSize={36} stroke="var(--surface)" strokeWidth={series.length > 1 ? 2 : 0}>
              {colorBy && data.map((row, j) => <Cell key={j} fill={colorBy(row)} />)}
            </Bar>
          ))}
          {series.length > 1 && <Legend iconType="square" wrapperStyle={{ fontSize: 12, color: "var(--ink-2)" }} />}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
