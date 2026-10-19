"use client";

import { Background, Controls, MarkerType, Position, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

export type LineageNode = { id: string; kind: "dataset" | "model" | "source"; label: string; layer: string; rows?: number | null; quality_status?: string; status?: string };
export type LineageEdge = { source: string; target: string; label: string };

const COLUMNS = ["source", "raw", "bronze", "silver", "gold", "features", "model"];
const QUALITY: Record<string, string> = { pass: "var(--ok)", warn: "var(--risk-medium)", fail: "var(--risk-high)", unchecked: "var(--line-strong)" };

/** Layered left-to-right layout: one column per medallion layer, models on the right. */
export function LineageGraph({ nodes, edges, focus }: { nodes: LineageNode[]; edges: LineageEdge[]; focus?: string | null }) {
  const router = useRouter();
  const { rfNodes, rfEdges } = useMemo(() => {
    const keep = new Set<string>();
    if (focus) {
      const up = new Map<string, string[]>();
      const down = new Map<string, string[]>();
      edges.forEach((e) => { up.set(e.target, [...(up.get(e.target) ?? []), e.source]); down.set(e.source, [...(down.get(e.source) ?? []), e.target]); });
      const walk = (start: string, m: Map<string, string[]>) => { const st = [start]; while (st.length) { const n = st.pop() as string; if (keep.has(n) && n !== start) continue; keep.add(n); (m.get(n) ?? []).forEach((x) => st.push(x)); } };
      walk(focus, up);
      walk(focus, down);
    }
    const shown = nodes.filter((n) => !focus || keep.has(n.id));
    const byCol = new Map<number, LineageNode[]>();
    shown.forEach((n) => { const c = Math.max(0, COLUMNS.indexOf(n.layer)); byCol.set(c, [...(byCol.get(c) ?? []), n]); });
    const rfNodes: Node[] = [];
    byCol.forEach((list, col) => {
      list.sort((a, b) => a.label.localeCompare(b.label)).forEach((n, i) => {
        const border = n.kind === "model" ? "var(--accent)" : n.kind === "source" ? "var(--ink-3)" : QUALITY[n.quality_status ?? "unchecked"];
        rfNodes.push({
          id: n.id, position: { x: col * 230, y: i * 74 },
          data: { label: <div className="text-left"><p className="text-[12px] font-medium leading-tight text-ink">{n.label}</p>
            <p className="text-[10.5px] text-ink-3">{n.kind === "dataset" ? `${n.layer}${n.rows != null ? ` · ${n.rows.toLocaleString()} lignes` : ""}` : n.kind === "model" ? `modèle · ${n.status}` : "source"}</p></div> },
          style: { width: 200, borderRadius: 8, border: `1.5px solid ${border}`, background: n.id === focus ? "var(--accent-soft)" : "var(--surface)", padding: 8, fontSize: 12 },
          sourcePosition: Position.Right, targetPosition: Position.Left,
        });
      });
    });
    const ids = new Set(rfNodes.map((n) => n.id));
    const rfEdges: Edge[] = edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e, i) => ({
      id: `e${i}`, source: e.source, target: e.target, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
      style: { stroke: "var(--line-strong)", strokeWidth: 1.3 }, label: undefined, data: { transform: e.label },
    }));
    return { rfNodes, rfEdges };
  }, [nodes, edges, focus]);
  return (
    <div className="h-[560px] w-full rounded-[var(--radius-control)] border border-line bg-surface-2">
      <ReactFlow nodes={rfNodes} edges={rfEdges} fitView minZoom={0.2} nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}
        onNodeClick={(_, n) => { if (!n.id.includes(":")) router.push(`/app/data/datasets/${n.id}`); }}>
        <Background gap={20} color="var(--grid)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
