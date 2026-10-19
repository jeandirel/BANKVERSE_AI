"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ExternalLink, Upload } from "lucide-react";
import dynamic from "next/dynamic";
import { useMemo, useRef, useState } from "react";
import { DataTable } from "@/components/ui/data-table";
import { ErrorState, KeyValue, Ledger, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Select } from "@/components/ui/primitives";
import { ApiError, api, get } from "@/lib/api";
import { qualityTone } from "@/lib/domain/data";
import { fmtCompact, fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";
import type { LineageEdge, LineageNode } from "@/components/lineage-graph";

const LineageGraph = dynamic(() => import("@/components/lineage-graph").then((m) => m.LineageGraph), { ssr: false });

type Source = { key: string; name: string; owner: string; kind: string; license: string; restrictions: string; original_url: string; modules: string[];
  access_status: string; access_note: string; commercial_use_ok: boolean; dataset_versions: number; last_version_at: string | null };
type Dataset = { id: string; name: string; layer: string; source_key: string | null; rows: number | null; columns: number | null; bytes: number | null; quality_status: string; created_at: string };
type Run = { id: string; pipeline: string; status: string; engine: string; started_at: string | null; finished_at: string | null; attempts: number; error: string | null; triggered_by: string;
  steps: { name: string; status: string; rows_out: number | null; duration_s: number | null; error: string | null }[] };
type Uploads = { templates: { key: string; label: string; required: Record<string, string> }[]; items: { id: string; name: string; fmt: string; rows: number; columns: number; quality_status: string; created_at: string }[] };
type UploadDetail = { id: string; name: string; rows: number; columns: number; schema: { name: string; dtype: string; kind: string }[];
  quality: { check: string; column: string | null; status: string; detail: string }[]; quality_status: string };

const KIND: Record<string, [string, string]> = { real_public: ["Données publiques réelles", "Real public data"], public_synthetic: ["Synthétique public", "Public synthetic"],
  demo_synthetic: ["Synthétique DEMO", "DEMO synthetic"], connector: ["Connecteur", "Connector"] };
const ACCESS: Record<string, [string, string]> = { ingested: ["Ingérée", "Ingested"], reachable: ["Accessible", "Reachable"], blocked: ["Bloquée ici", "Blocked here"],
  connector_only: ["Connecteur (clé requise)", "Connector (key required)"], unknown: ["Inconnu", "Unknown"], generated: ["Générée", "Generated"] };

function UploadDialog({ open, onOpenChange, templates }: { open: boolean; onOpenChange: (v: boolean) => void; templates: Uploads["templates"] }) {
  const L = useL();
  const toast = useToast();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [template, setTemplate] = useState("generic");
  const [result, setResult] = useState<UploadDetail | null>(null);
  const send = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append("file", file as File); fd.append("template", template); return api<UploadDetail>("/data/uploads", { method: "POST", body: fd }); },
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ["data", "uploads"] }); },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  const tpl = templates.find((t) => t.key === template);
  return (
    <Dialog wide open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) { setResult(null); setFile(null); } }} title={L("Importer un jeu de données", "Upload a dataset")}
      description={L("CSV ou Parquet, 15 Mo maximum. Le fichier est profilé et contrôlé ; il reste privé à votre organisation.", "CSV or Parquet, 15 MB max. The file is profiled and checked; it stays private to your organisation.")}
      footer={result ? <Button onClick={() => onOpenChange(false)}>{L("Fermer", "Close")}</Button> : <><Button onClick={() => onOpenChange(false)}>{L("Annuler", "Cancel")}</Button>
        <Button variant="primary" disabled={!file} loading={send.isPending} onClick={() => send.mutate()}>{L("Importer et contrôler", "Upload and check")}</Button></>}>
      {result ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm">{result.name} — {fmtNumber(result.rows, "fr")} {L("lignes", "rows")}, {result.columns} {L("colonnes", "columns")} · <Badge tone={qualityTone(result.quality_status)}>{result.quality_status}</Badge></p>
          <ul className="divide-y divide-line text-sm">{result.quality.map((c, i) => (
            <li key={i} className="flex gap-2 py-1.5"><Badge tone={qualityTone(c.status)}>{c.status}</Badge><span className="font-medium">{c.check}</span>{c.column && <span className="text-ink-3">{c.column}</span>}<span className="ml-auto text-ink-2">{c.detail}</span></li>))}</ul>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <Field label={L("Modèle de données attendu", "Expected data template")} htmlFor="up-t">
            <Select id="up-t" value={template} onChange={(e) => setTemplate(e.target.value)}>{templates.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</Select>
          </Field>
          {tpl && Object.keys(tpl.required).length > 0 && <p className="text-xs text-ink-3">{L("Colonnes requises : ", "Required columns: ")}{Object.entries(tpl.required).map(([k, v]) => `${k} (${v})`).join(", ")}</p>}
          <Button icon={<Upload className="size-4" />} onClick={() => input.current?.click()}>{file ? file.name : L("Choisir un fichier", "Choose a file")}</Button>
          <input ref={input} type="file" accept=".csv,.parquet" className="sr-only" aria-label={L("Fichier", "File")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <Notice tone="warn">{L("N'importez pas de données personnelles réelles dans un environnement de démonstration.", "Do not upload real personal data into a demo environment.")}</Notice>
        </div>
      )}
    </Dialog>
  );
}

export default function DataPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const [tab, setTab] = useState("catalog");
  const [layer, setLayer] = useState("");
  const [focus, setFocus] = useState("");
  const [upOpen, setUpOpen] = useState(false);
  const pick2 = (p?: [string, string]) => (p ? (locale === "en" ? p[1] : p[0]) : "—");
  const srcs = useQuery({ queryKey: ["data", "sources"], queryFn: () => get<Source[]>("/data/sources") });
  const ds = useQuery({ queryKey: ["data", "datasets"], queryFn: () => get<Dataset[]>("/data/datasets") });
  const lin = useQuery({ queryKey: ["data", "lineage"], queryFn: () => get<{ nodes: LineageNode[]; edges: LineageEdge[] }>("/data/lineage"), enabled: tab === "lineage" });
  const runs = useQuery({ queryKey: ["data", "runs"], queryFn: () => get<Run[]>("/data/pipelines/runs"), enabled: tab === "pipelines" });
  const ups = useQuery({ queryKey: ["data", "uploads"], queryFn: () => get<Uploads>("/data/uploads") });
  const dcols = useMemo<ColumnDef<Dataset, unknown>[]>(() => [
    { accessorKey: "name", header: L("Jeu de données", "Dataset"), cell: ({ row }) => <div><p className="font-medium">{row.original.name}</p><p className="text-xs text-ink-3">{row.original.source_key}</p></div> },
    { accessorKey: "layer", header: L("Couche", "Layer"), cell: ({ getValue }) => <Badge>{String(getValue())}</Badge> },
    { accessorKey: "rows", header: L("Lignes", "Rows"), meta: { align: "right" }, cell: ({ getValue }) => fmtNumber(getValue() as number | null, locale) },
    { accessorKey: "columns", header: L("Colonnes", "Columns"), meta: { align: "right" } },
    { accessorKey: "bytes", header: L("Taille", "Size"), meta: { align: "right" }, cell: ({ getValue }) => getValue() == null ? "—" : `${fmtCompact(Number(getValue()), locale)}o` },
    { accessorKey: "quality_status", header: L("Qualité", "Quality"), cell: ({ getValue }) => <Badge tone={qualityTone(String(getValue()))}>{String(getValue())}</Badge> },
    { accessorKey: "created_at", header: L("Version du", "Version of"), cell: ({ getValue }) => fmtDate(String(getValue()), locale, true) },
  ], [L, locale]);
  const data = (ds.data ?? []).filter((d) => !layer || d.layer === layer);
  const real = (srcs.data ?? []).filter((s) => s.kind === "real_public");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.data")} subtitle={L("Sources vérifiées, couches bronze / silver / gold, contrôles qualité, traçabilité jusqu'aux modèles.", "Verified sources, bronze / silver / gold layers, quality checks, lineage up to the models.")}
        actions={can("data.manage") ? <Button variant="primary" icon={<Upload className="size-4" />} onClick={() => setUpOpen(true)}>{L("Importer des données", "Upload data")}</Button> : undefined} />
      {ds.data && srcs.data && <Ledger items={[
        { label: L("Sources publiques réelles", "Real public sources"), value: `${real.filter((s) => s.dataset_versions > 0).length} / ${real.length}`, hint: L("ingérées / référencées", "ingested / registered") },
        { label: L("Jeux de données courants", "Current datasets"), value: fmtNumber(ds.data.length, locale) },
        { label: L("Lignes (gold)", "Rows (gold)"), value: fmtCompact(ds.data.filter((d) => d.layer === "gold").reduce((a, d) => a + (d.rows ?? 0), 0), locale) },
        { label: L("Contrôles en échec", "Failing checks"), value: fmtNumber(ds.data.filter((d) => d.quality_status === "fail").length, locale), tone: ds.data.some((d) => d.quality_status === "fail") ? "high" : "ok" },
      ]} />}
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "catalog", label: L("Catalogue", "Catalogue") }, { value: "sources", label: L("Sources et licences", "Sources & licences") },
        { value: "lineage", label: L("Lignage", "Lineage") }, { value: "pipelines", label: L("Pipelines", "Pipelines") }, { value: "uploads", label: L("Données de la banque", "Bank data"), count: ups.data?.items.length }]}>
        <TabPanel value="catalog" className="pt-4">
          <Panel flush actions={<Select className="h-8 w-36" value={layer} onChange={(e) => setLayer(e.target.value)} aria-label={L("Couche", "Layer")}>
            <option value="">{L("Toutes couches", "All layers")}</option>{["raw", "bronze", "silver", "gold"].map((l) => <option key={l}>{l}</option>)}</Select>}>
            {ds.error ? <ErrorState error={ds.error} /> : <DataTable columns={dcols} data={data} loading={ds.isLoading} rowHref={(r) => `/app/data/datasets/${r.id}`} clientPageSize={25} />}
          </Panel>
        </TabPanel>
        <TabPanel value="sources" className="pt-4">
          {srcs.error ? <ErrorState error={srcs.error} /> : !srcs.data ? <LoadingBlock /> : (
            <ul className="grid gap-4 lg:grid-cols-2">{srcs.data.map((s) => (
              <li key={s.key} className="rounded-[var(--radius-panel)] border border-line bg-surface p-4">
                <div className="flex flex-wrap items-start gap-2"><h3 className="flex-1 font-semibold text-[15px] leading-snug">{s.name}</h3>
                  <Badge tone={s.kind === "real_public" ? "info" : s.kind === "demo_synthetic" ? "demo" : "neutral"}>{pick2(KIND[s.kind])}</Badge>
                  <Badge tone={s.access_status === "ingested" || s.dataset_versions > 0 ? "ok" : s.access_status === "blocked" ? "high" : "medium"}>{pick2(ACCESS[s.dataset_versions > 0 ? "ingested" : s.access_status] ?? [s.access_status, s.access_status])}</Badge></div>
                <KeyValue className="mt-3" rows={[[L("Propriétaire", "Owner"), s.owner], [L("Licence", "Licence"), s.license], [L("Usage commercial", "Commercial use"), s.commercial_use_ok ? L("autorisé", "allowed") : L("non vérifié / restreint", "not verified / restricted")],
                  [L("Restrictions", "Restrictions"), s.restrictions || "—"], [L("Modules", "Modules"), s.modules.join(", ")], [L("Versions", "Versions"), String(s.dataset_versions)],
                  ...(s.access_note ? [[L("Accès", "Access"), s.access_note] as [string, string]] : [])]} />
                {s.original_url.startsWith("http") && <a href={s.original_url} target="_blank" rel="noreferrer noopener" className="mt-3 inline-flex items-center gap-1 text-sm text-accent hover:underline">{L("Source d'origine", "Original source")}<ExternalLink className="size-3.5" /></a>}
              </li>))}</ul>
          )}
        </TabPanel>
        <TabPanel value="lineage" className="pt-4">
          <Panel title={L("Lignage des données et des modèles", "Data and model lineage")} description={L("Source → raw → bronze → silver → gold → modèle. Cliquez sur un jeu de données pour le détail.", "Source → raw → bronze → silver → gold → model. Click a dataset for details.")}
            actions={<Select className="h-8 w-56" value={focus} onChange={(e) => setFocus(e.target.value)} aria-label={L("Focaliser", "Focus")}>
              <option value="">{L("Tout le lignage", "Whole lineage")}</option>{lin.data?.nodes.filter((n) => n.kind !== "source").map((n) => <option key={n.id} value={n.id}>{n.label} ({n.layer})</option>)}</Select>}>
            {lin.error ? <ErrorState error={lin.error} /> : !lin.data ? <LoadingBlock /> : <LineageGraph nodes={lin.data.nodes} edges={lin.data.edges} focus={focus || null} />}
          </Panel>
        </TabPanel>
        <TabPanel value="pipelines" className="pt-4">
          {runs.error ? <ErrorState error={runs.error} /> : !runs.data ? <LoadingBlock /> : (
            <Panel flush description={L("Exécutions suivies (relances automatiques, étapes, volumes). Déclenchement par l'orchestrateur ou la CLI d'exploitation.", "Tracked runs (automatic retries, steps, volumes). Triggered by the orchestrator or the operations CLI.")}>
              <ul>{runs.data.map((r) => (
                <li key={r.id} className="border-b border-line px-4 py-3 last:border-0">
                  <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{r.pipeline}</span><Badge tone={r.status === "succeeded" ? "ok" : r.status === "failed" ? "high" : "medium"}>{r.status}</Badge>
                    <Badge>{r.engine}</Badge>{r.attempts > 1 && <Badge tone="medium">{r.attempts} {L("tentatives", "attempts")}</Badge>}
                    <span className="ml-auto text-xs text-ink-3">{fmtDate(r.started_at, locale, true)}</span></div>
                  {r.error && <p className="mt-1 text-xs text-risk-high line-clamp-2">{r.error}</p>}
                  {r.steps.length > 0 && <p className="mt-1 text-xs text-ink-3">{r.steps.map((s) => `${s.name} (${s.status}${s.rows_out != null ? `, ${fmtCompact(s.rows_out, locale)}` : ""}${s.duration_s != null ? `, ${s.duration_s}s` : ""})`).join(" → ")}</p>}
                </li>))}</ul>
            </Panel>
          )}
        </TabPanel>
        <TabPanel value="uploads" className="pt-4">
          <Panel flush description={L("Jeux importés par votre banque : profilage et contrôles de qualité. Données isolées par organisation.", "Datasets uploaded by your bank: profiling and quality checks. Data isolated per organisation.")}>
            {!ups.data ? <LoadingBlock /> : ups.data.items.length === 0 ? <p className="px-4 py-6 text-sm text-ink-3">{L("Aucun import.", "No upload.")}</p> : (
              <ul>{ups.data.items.map((u) => (
                <li key={u.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5 text-sm last:border-0">
                  <span className="font-medium">{u.name}</span><span className="text-ink-3">{fmtNumber(u.rows, locale)} × {u.columns}</span>
                  <Badge tone={qualityTone(u.quality_status)}>{u.quality_status}</Badge><span className="ml-auto text-xs text-ink-3">{fmtDate(u.created_at, locale, true)}</span>
                </li>))}</ul>
            )}
          </Panel>
        </TabPanel>
      </Tabs>
      {ups.data && <UploadDialog open={upOpen} onOpenChange={setUpOpen} templates={ups.data.templates} />}
    </div>
  );
}
