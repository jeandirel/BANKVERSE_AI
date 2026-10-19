"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ErrorState, KeyValue, LoadingBlock, PageHeader, Panel } from "@/components/ui/layout";
import { Badge } from "@/components/ui/primitives";
import { get } from "@/lib/api";
import { qualityTone } from "@/lib/domain/data";
import { fmtCompact, fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

type ColProfile = { dtype?: string; null_rate?: number; distinct?: number | null; min?: number | string | null; max?: number | string | null; mean?: number | null; std?: number | null; top?: unknown };
type Detail = { id: string; name: string; layer: string; source_key: string | null; rows: number | null; columns: number | null; bytes: number | null; fingerprint: string;
  quality_status: string; created_at: string; is_current: boolean; schema: { name: string; dtype: string }[]; profile: { columns?: Record<string, ColProfile>; rows?: number } & Record<string, unknown>;
  quality: { check: string; column: string | null; status: string; observed: number | null; threshold: number | null }[];
  upstream: { id: string; name: string; layer: string; transform: string }[]; downstream: { id: string; name: string; layer: string; transform: string }[];
  versions: { id: string; fingerprint: string; rows: number | null; created_at: string; is_current: boolean; quality_status: string }[];
  models: { name: string; version: number; status: string }[]; run: { pipeline: string; status: string; engine: string; finished_at: string | null } | null };

function fmtVal(v: unknown, locale: string): string {
  if (v == null) return "—";
  if (typeof v === "number") return Math.abs(v) >= 1000 ? fmtNumber(v, locale as "fr" | "en", 0) : fmtNumber(v, locale as "fr" | "en", 3);
  return String(v).slice(0, 40);
}

export default function DatasetPage() {
  const { id } = useParams<{ id: string }>();
  const { locale } = useI18n();
  const L = useL();
  const q = useQuery({ queryKey: ["data", "dataset", id], queryFn: () => get<Detail>(`/data/datasets/${id}`) });
  if (q.error) return <ErrorState error={q.error} />;
  const d = q.data;
  if (!d) return <LoadingBlock />;
  const cols = d.profile.columns ?? {};
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/data" className="inline-flex items-center gap-1.5 text-sm text-ink-2 hover:text-ink w-fit"><ArrowLeft className="size-4" />{L("Plateforme de données", "Data platform")}</Link>
      <PageHeader title={d.name} subtitle={`${d.layer} · ${d.source_key ?? "—"} · ${fmtDate(d.created_at, locale, true)}`}
        meta={<div className="flex gap-2"><Badge>{d.layer}</Badge><Badge tone={qualityTone(d.quality_status)}>{L("qualité", "quality")} {d.quality_status}</Badge>{!d.is_current && <Badge tone="medium">{L("version antérieure", "older version")}</Badge>}</div>} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,1fr)]">
        <div className="flex flex-col gap-6 min-w-0">
          <Panel title={L("Schéma et profil", "Schema and profile")} flush>
            <div className="overflow-x-auto"><table className="w-full text-sm">
              <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">{[L("Colonne", "Column"), L("Type", "Type"), L("Manquants", "Missing"), L("Distincts", "Distinct"), "Min", "Max", L("Moyenne", "Mean")].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>{d.schema.map((c) => { const p = cols[c.name] ?? {}; return (
                <tr key={c.name} className="border-b border-line"><td className="px-3 py-1.5 font-medium">{c.name}</td><td className="px-3 py-1.5 text-ink-3">{c.dtype}</td>
                  <td className="px-3 py-1.5 num">{p.null_rate == null ? "—" : `${(p.null_rate * 100).toFixed(1)} %`}</td><td className="px-3 py-1.5 num">{fmtVal(p.distinct, locale)}</td>
                  <td className="px-3 py-1.5 num">{fmtVal(p.min, locale)}</td><td className="px-3 py-1.5 num">{fmtVal(p.max, locale)}</td><td className="px-3 py-1.5 num">{fmtVal(p.mean, locale)}</td></tr>); })}</tbody>
            </table></div>
          </Panel>
          <Panel title={L("Contrôles qualité", "Quality checks")} flush>
            {d.quality.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Aucun contrôle enregistré pour cette version.", "No check recorded for this version.")}</p> :
              <ul>{d.quality.map((c, i) => (
                <li key={i} className="flex items-center gap-3 border-b border-line px-4 py-2 text-sm last:border-0">
                  <Badge tone={qualityTone(c.status)}>{c.status}</Badge><span className="font-medium">{c.check}</span>{c.column && <span className="text-ink-3">{c.column}</span>}
                  <span className="ml-auto num text-ink-2">{c.observed != null ? fmtVal(c.observed, locale) : ""}{c.threshold != null ? ` / ${fmtVal(c.threshold, locale)}` : ""}</span>
                </li>))}</ul>}
          </Panel>
        </div>
        <div className="flex flex-col gap-6">
          <Panel title={L("Identité", "Identity")}>
            <KeyValue rows={[[L("Lignes", "Rows"), fmtNumber(d.rows, locale)], [L("Colonnes", "Columns"), String(d.columns ?? "—")], [L("Taille", "Size"), d.bytes ? `${fmtCompact(d.bytes, locale)}o` : "—"],
              [L("Empreinte", "Fingerprint"), <span key="f" className="num text-xs break-all">{d.fingerprint}</span>],
              [L("Produit par", "Produced by"), d.run ? `${d.run.pipeline} (${d.run.engine}, ${d.run.status})` : "—"]]} />
          </Panel>
          <Panel title={L("Lignage", "Lineage")}>
            <p className="text-xs text-ink-3 mb-1">{L("En amont", "Upstream")}</p>
            <ul className="mb-3 text-sm">{d.upstream.length ? d.upstream.map((u) => <li key={u.id}><Link className="text-accent hover:underline" href={`/app/data/datasets/${u.id}`}>{u.name}</Link> <span className="text-ink-3">({u.layer}) — {u.transform}</span></li>) : <li className="text-ink-3">—</li>}</ul>
            <p className="text-xs text-ink-3 mb-1">{L("En aval", "Downstream")}</p>
            <ul className="mb-3 text-sm">{d.downstream.length ? d.downstream.map((u) => <li key={u.id}><Link className="text-accent hover:underline" href={`/app/data/datasets/${u.id}`}>{u.name}</Link> <span className="text-ink-3">({u.layer}) — {u.transform}</span></li>) : <li className="text-ink-3">—</li>}</ul>
            <p className="text-xs text-ink-3 mb-1">{L("Modèles entraînés sur cette version", "Models trained on this version")}</p>
            <ul className="text-sm">{d.models.length ? d.models.map((m) => <li key={`${m.name}${m.version}`}><Link className="text-accent hover:underline" href={`/app/models/${m.name}`}>{m.name} v{m.version}</Link> <span className="text-ink-3">({m.status})</span></li>) : <li className="text-ink-3">—</li>}</ul>
          </Panel>
          <Panel title={L("Versions", "Versions")} flush>
            <ul>{d.versions.map((v) => (
              <li key={v.id} className="flex items-center gap-2 border-b border-line px-4 py-2 text-sm last:border-0">
                <Link href={`/app/data/datasets/${v.id}`} className="num text-xs text-accent hover:underline">{v.fingerprint}</Link>
                {v.is_current && <Badge tone="ok">{L("courante", "current")}</Badge>}<span className="ml-auto text-xs text-ink-3">{fmtDate(v.created_at, locale, true)}</span>
              </li>))}</ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}
