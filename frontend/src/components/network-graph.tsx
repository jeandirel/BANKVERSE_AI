"use client";

import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Building2, Globe2, Landmark } from "lucide-react";
import { useMemo } from "react";
import { cn } from "@/lib/cn";
import { fmtMoney } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

export type NetNode = {
  id: string; kind: "account" | "external"; label: string; iban: string | null; country?: string | null; customer_ref?: string | null;
  ml_score?: number | null; ml_percentile?: number | null; community?: number | null; alert_id?: string | null;
  alert_band?: string | null; typologies?: string[]; is_focus?: boolean;
};
export type NetEdge = { source: string; target: string; total: number; count: number; first: string; last: string; suspicious: boolean };

const bandVar: Record<string, string> = { critical: "var(--risk-critical)", high: "var(--risk-high)", medium: "var(--risk-medium)" };

function AccountNode({ data }: NodeProps<Node<NetNode & { selected?: boolean }>>) {
  const d = data;
  const ext = d.kind === "external";
  return (
    <div className={cn("rounded-[5px] border bg-surface px-2.5 py-1.5 text-left shadow-sm min-w-[150px] max-w-[190px]",
      ext ? "border-dashed border-line-strong" : "border-line-strong", d.is_focus && "ring-2 ring-accent")}
      style={d.alert_band ? { borderLeft: `4px solid ${bandVar[d.alert_band]}` } : undefined}>
      <Handle type="target" position={Position.Left} className="!size-1.5 !bg-ink-3 !border-0" />
      <div className="flex items-center gap-1.5 text-[11.5px] text-ink-3">
        {ext ? (d.country && d.country !== "FR" ? <Globe2 className="size-3" /> : <Building2 className="size-3" />) : <Landmark className="size-3" />}
        <span className="truncate">{ext ? (d.country ?? "EXT") : d.customer_ref}</span>
      </div>
      <p className="truncate text-[12.5px] font-medium text-ink">{d.label}</p>
      {!ext && d.ml_percentile != null && <p className="num text-[11px] text-ink-3">rang {Math.round(d.ml_percentile * 1000) / 10} %</p>}
      <Handle type="source" position={Position.Right} className="!size-1.5 !bg-ink-3 !border-0" />
    </div>
  );
}

const nodeTypes = { acct: AccountNode };

/** Radial layout: focus account at the centre, direct counterparties on the first ring, others outside. */
function layout(nodes: NetNode[], edges: NetEdge[]): Record<string, { x: number; y: number }> {
  const focus = nodes.find((n) => n.is_focus)?.id ?? nodes[0]?.id;
  const adj = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!adj.has(e.source)) adj.set(e.source, new Set());
    if (!adj.has(e.target)) adj.set(e.target, new Set());
    adj.get(e.source)!.add(e.target);
    adj.get(e.target)!.add(e.source);
  }
  const level = new Map<string, number>([[focus, 0]]);
  const queue = [focus];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const nb of adj.get(cur) ?? []) {
      if (!level.has(nb)) {
        level.set(nb, (level.get(cur) ?? 0) + 1);
        queue.push(nb);
      }
    }
  }
  const rings = new Map<number, string[]>();
  for (const n of nodes) {
    const l = level.get(n.id) ?? 3;
    rings.set(l, [...(rings.get(l) ?? []), n.id]);
  }
  const pos: Record<string, { x: number; y: number }> = {};
  for (const [l, ids] of rings) {
    const r = l === 0 ? 0 : 260 * l;
    ids.forEach((id, i) => {
      const a = (2 * Math.PI * i) / Math.max(ids.length, 1) + (l % 2 ? 0 : Math.PI / Math.max(ids.length, 1));
      pos[id] = { x: Math.round(r * Math.cos(a)), y: Math.round(r * 0.72 * Math.sin(a)) };
    });
  }
  return pos;
}

export function NetworkGraph({ nodes, edges, onSelect, height = 520 }: {
  nodes: NetNode[]; edges: NetEdge[]; onSelect?: (n: NetNode) => void; height?: number;
}) {
  const { locale } = useI18n();
  const maxTotal = Math.max(1, ...edges.map((e) => e.total));
  const { rfNodes, rfEdges } = useMemo(() => {
    const pos = layout(nodes, edges);
    return {
      rfNodes: nodes.map<Node<NetNode>>((n) => ({ id: n.id, type: "acct", position: pos[n.id] ?? { x: 0, y: 0 }, data: n })),
      rfEdges: edges.map<Edge>((e, i) => ({
        id: `e${i}`, source: e.source, target: e.target,
        label: `${fmtMoney(e.total, locale, "EUR", 0)} · ${e.count}×`,
        labelStyle: { fontSize: 10.5, fill: "var(--ink-2)" },
        labelBgStyle: { fill: "var(--surface)" },
        style: { stroke: e.suspicious ? "var(--risk-high)" : "var(--line-strong)", strokeWidth: 1 + 3 * Math.sqrt(e.total / maxTotal),
          strokeDasharray: e.suspicious ? undefined : "4 3" },
        markerEnd: { type: MarkerType.ArrowClosed, color: e.suspicious ? "var(--risk-high)" : "var(--line-strong)", width: 14, height: 14 },
      })),
    };
  }, [nodes, edges, locale, maxTotal]);
  return (
    <div style={{ height }} className="w-full rounded border border-line bg-surface-2">
      <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} fitView minZoom={0.2} maxZoom={1.8}
        nodesConnectable={false} onNodeClick={(_, n) => onSelect?.(n.data as NetNode)} proOptions={{ hideAttribution: true }}>
        <Background color="var(--line)" gap={22} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
