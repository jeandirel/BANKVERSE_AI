"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, RotateCcw, Rocket, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { BarsChart } from "@/components/ui/charts";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Textarea } from "@/components/ui/primitives";
import { ApiError, get, post } from "@/lib/api";
import { driftTone, MODULE_LABELS, VERSION_STATUS, versionTone } from "@/lib/domain/models";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type Version = { version: number; status: string; algorithm: string; created_at: string; trained_by: string; training_seconds: number | null; git_commit: string | null;
  artifact_sha256: string; mlflow_run_id: string | null; headline: { label: string; value: number | string } | null; params: Record<string, unknown>; features: string[];
  model_card: { intended_use?: string; training_data?: string; limitations?: string[]; human_oversight?: string; [k: string]: unknown };
  approvals: { decision: string; reviewer: string; comment: string; at: string }[]; datasets: { id: string; name: string; layer: string; rows: number | null; fingerprint: string }[] };
type Detail = { name: string; module: string; task: string; description: string; risk_tier: string; can_manage_lifecycle: boolean; versions: Version[];
  deployments: { action: string; version: number | null; previous_version: number | null; actor: string; reason: string; active: boolean; at: string }[]; notice: string };
type Drift = { status: string; max_psi?: number | null; samples?: number; note?: string; model_version?: string;
  features?: { feature: string; psi: number; status: string }[]; live: { inferences: number; error_rate: number | null; mean_latency_ms: number | null } };
type Metrics = { metrics: Record<string, unknown> };

function MetricsView({ m }: { m: Record<string, unknown> }) {
  const L = useL();
  const test = m.test as Record<string, number> | undefined;
  const comparison = m.comparison as Record<string, unknown>[] | undefined;
  const scalar = test ? Object.entries(test).filter(([, v]) => typeof v === "number") : [];
  return (
    <div className="flex flex-col gap-6">
      {scalar.length > 0 && <Panel title={L("Champion — jeu de test", "Champion — test set")}><KeyValue rows={scalar.map(([k, v]) => [k, Number(v).toFixed(4)])} /></Panel>}
      {Array.isArray(comparison) && comparison.length > 0 && typeof comparison[0] === "object" && (
        <Panel title={L("Comparaison des modèles", "Model comparison")} flush>
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">{Object.keys(comparison[0]).filter((k) => !["pr_curve", "roc_curve"].includes(k)).map((k) => <th key={k} className="px-3 py-2 font-medium">{k}</th>)}</tr></thead>
            <tbody>{comparison.map((row, i) => (
              <tr key={i} className="border-b border-line">{Object.entries(row).filter(([k]) => !["pr_curve", "roc_curve"].includes(k)).map(([k, v]) => (
                <td key={k} className="px-3 py-1.5 num">{typeof v === "number" ? v.toFixed(4) : typeof v === "object" ? JSON.stringify(v).slice(0, 60) : String(v)}</td>))}</tr>))}</tbody>
          </table></div>
        </Panel>
      )}
      <Panel title={L("Métriques complètes (JSON)", "Full metrics (JSON)")}>
        <pre className="max-h-[480px] overflow-auto rounded bg-surface-2 p-3 text-[11.5px] leading-snug">{JSON.stringify(m, (k, v) => (k === "pr_curve" || k === "roc_curve" ? `[${(v as unknown[]).length} points]` : v), 2)}</pre>
      </Panel>
    </div>
  );
}

export default function ModelPage() {
  const { name } = useParams<{ name: string }>();
  const { locale } = useI18n();
  const L = useL();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("versions");
  const [sel, setSel] = useState<number | null>(null);
  const [action, setAction] = useState<null | { kind: "approved" | "rejected" | "deploy" | "rollback"; version?: number }>(null);
  const [comment, setComment] = useState("");
  const q = useQuery({ queryKey: ["models", name], queryFn: () => get<Detail>(`/models/${name}`) });
  const drift = useQuery({ queryKey: ["models", name, "drift"], queryFn: () => get<Drift>(`/models/${name}/drift`), enabled: tab === "drift" });
  const version = sel ?? q.data?.versions[0]?.version;
  const metrics = useQuery({ queryKey: ["models", name, "metrics", version], queryFn: () => get<Metrics>(`/models/${name}/versions/${version}/metrics`), enabled: tab === "metrics" && version != null });
  const act = useMutation({
    mutationFn: () => {
      if (!action) throw new Error("no action");
      if (action.kind === "rollback") return post(`/models/${name}/rollback`, { reason: comment });
      if (action.kind === "deploy") return post(`/models/${name}/versions/${action.version}/deploy`, { reason: comment });
      return post(`/models/${name}/versions/${action.version}/review`, { decision: action.kind, comment });
    },
    onSuccess: () => { setAction(null); setComment(""); qc.invalidateQueries({ queryKey: ["models"] }); toast(L("Opération enregistrée", "Operation recorded")); },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  if (q.error) return <ErrorState error={q.error} />;
  const d = q.data;
  if (!d) return <LoadingBlock />;
  const prod = d.versions.find((v) => v.status === "production");
  const manage = d.can_manage_lifecycle;
  const cur = d.versions.find((v) => v.version === version);
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/models" className="inline-flex items-center gap-1.5 text-sm text-ink-2 hover:text-ink w-fit"><ArrowLeft className="size-4" />{L("Registre des modèles", "Model registry")}</Link>
      <PageHeader title={d.name} subtitle={d.description}
        meta={<div className="flex flex-wrap gap-2"><Badge>{locale === "en" ? MODULE_LABELS[d.module]?.[1] : MODULE_LABELS[d.module]?.[0]}</Badge><Badge tone={d.risk_tier === "high" ? "high" : "neutral"}>{L("risque", "risk")} {d.risk_tier}</Badge>
          {prod ? <Badge tone="ok">{L("production", "production")} v{prod.version}</Badge> : <Badge tone="medium">{L("non déployé", "not deployed")}</Badge>}</div>}
        actions={manage && can("models.deploy") && d.deployments.some((x) => x.active && x.previous_version) ? <Button icon={<RotateCcw className="size-4" />} onClick={() => setAction({ kind: "rollback" })}>{L("Revenir à la version précédente", "Roll back")}</Button> : undefined} />
      <Notice>{d.notice}</Notice>
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "versions", label: L("Versions", "Versions"), count: d.versions.length }, { value: "card", label: L("Fiche modèle", "Model card") },
        { value: "metrics", label: L("Métriques", "Metrics") }, { value: "drift", label: L("Dérive en production", "Production drift") }, { value: "deployments", label: L("Déploiements", "Deployments") }]}>
        <TabPanel value="versions" className="pt-4">
          <Panel flush>
            <ul>{d.versions.map((v) => (
              <li key={v.version} className="border-b border-line px-4 py-3 last:border-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">v{v.version}</span><Badge tone={versionTone(v.status)}>{locale === "en" ? VERSION_STATUS[v.status]?.[1] : VERSION_STATUS[v.status]?.[0]}</Badge>
                  <span className="text-sm text-ink-2">{v.algorithm}</span>
                  {v.headline && <span className="text-sm text-ink-3">· {v.headline.label} <span className="num text-ink">{typeof v.headline.value === "number" ? v.headline.value.toFixed(4) : v.headline.value}</span></span>}
                  <span className="ml-auto text-xs text-ink-3">{L("entraînée par", "trained by")} {v.trained_by} · {fmtDate(v.created_at, locale, true)}</span>
                </div>
                <p className="mt-1 text-xs text-ink-3">SHA-256 {v.artifact_sha256.slice(0, 16)}… · {v.training_seconds ? `${fmtNumber(v.training_seconds / 60, locale, 1)} min` : ""}{v.git_commit ? ` · commit ${v.git_commit.slice(0, 8)}` : ""}{v.datasets.length ? ` · ${L("données", "data")} : ${v.datasets.map((x) => `${x.name} (${x.fingerprint})`).join(", ")}` : ""}</p>
                {v.approvals.map((a, i) => <p key={i} className="mt-1 text-xs"><Badge tone={a.decision === "approved" ? "ok" : "high"}>{a.decision}</Badge> <span className="text-ink-2">{a.reviewer} — « {a.comment} »</span></p>)}
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button size="sm" variant="ghost" onClick={() => { setSel(v.version); setTab("metrics"); }}>{L("Métriques", "Metrics")}</Button>
                  <Button size="sm" variant="ghost" onClick={() => { setSel(v.version); setTab("card"); }}>{L("Fiche", "Card")}</Button>
                  {manage && can("models.approve") && v.status === "candidate" && <>
                    <Button size="sm" icon={<X className="size-3.5" />} onClick={() => setAction({ kind: "rejected", version: v.version })}>{L("Rejeter", "Reject")}</Button>
                    <Button size="sm" icon={<Check className="size-3.5" />} onClick={() => setAction({ kind: "approved", version: v.version })}>{L("Approuver", "Approve")}</Button></>}
                  {manage && can("models.deploy") && v.status === "approved" && <Button size="sm" variant="primary" icon={<Rocket className="size-3.5" />} onClick={() => setAction({ kind: "deploy", version: v.version })}>{L("Déployer", "Deploy")}</Button>}
                </div>
              </li>))}</ul>
          </Panel>
        </TabPanel>
        <TabPanel value="card" className="pt-4">
          {!cur ? <LoadingBlock /> : (
            <Panel title={L(`Fiche modèle — v${cur.version}`, `Model card — v${cur.version}`)}>
              <KeyValue rows={[[L("Usage prévu", "Intended use"), cur.model_card.intended_use ?? "—"], [L("Données d'entraînement", "Training data"), cur.model_card.training_data ?? "—"],
                [L("Supervision humaine", "Human oversight"), cur.model_card.human_oversight ?? L("Décision finale humaine", "Final decision by a human")],
                [L("Variables", "Features"), cur.features.slice(0, 40).join(", ") + (cur.features.length > 40 ? ` (+${cur.features.length - 40})` : "")],
                [L("Hyperparamètres", "Hyperparameters"), <pre key="p" className="whitespace-pre-wrap text-xs">{JSON.stringify(cur.params, null, 1).slice(0, 1200)}</pre>]]} />
              {cur.model_card.limitations && cur.model_card.limitations.length > 0 && <div className="mt-4"><p className="text-sm font-medium mb-1">{L("Limites connues", "Known limitations")}</p>
                <ul className="list-disc pl-5 text-sm text-ink-2">{cur.model_card.limitations.map((x) => <li key={x}>{x}</li>)}</ul></div>}
            </Panel>
          )}
        </TabPanel>
        <TabPanel value="metrics" className="pt-4">
          <p className="mb-3 text-sm text-ink-3">{L(`Version v${version}`, `Version v${version}`)}</p>
          {metrics.error ? <ErrorState error={metrics.error} /> : !metrics.data ? <LoadingBlock /> : <MetricsView m={metrics.data.metrics} />}
        </TabPanel>
        <TabPanel value="drift" className="pt-4">
          {drift.error ? <ErrorState error={drift.error} /> : !drift.data ? <LoadingBlock /> : (
            <div className="flex flex-col gap-6">
              <Panel title={L("Trafic de votre organisation (30 jours)", "Your organisation's traffic (30 days)")}>
                <KeyValue rows={[[L("Inférences", "Inferences"), fmtNumber(drift.data.live.inferences, locale)], [L("Taux d'erreur", "Error rate"), drift.data.live.error_rate == null ? "—" : `${(drift.data.live.error_rate * 100).toFixed(2)} %`],
                  [L("Latence moyenne", "Mean latency"), drift.data.live.mean_latency_ms == null ? "—" : `${drift.data.live.mean_latency_ms} ms`],
                  [L("Statut de dérive", "Drift status"), <Badge key="d" tone={driftTone(drift.data.status)}>{drift.data.status}</Badge>], [L("PSI max", "Max PSI"), drift.data.max_psi?.toFixed(4) ?? "—"]]} />
                {drift.data.note && <p className="mt-3 text-sm text-ink-3">{drift.data.note}</p>}
                <p className="mt-3 text-xs text-ink-3">{L("Lecture usuelle du PSI : < 0,1 stable · 0,1–0,25 modérée · > 0,25 significative (réentraînement à étudier).", "Usual PSI reading: < 0.1 stable · 0.1–0.25 moderate · > 0.25 significant (consider retraining).")}</p>
              </Panel>
              {drift.data.features && drift.data.features.length > 0 && <Panel title={L("PSI par variable", "PSI per feature")}>
                <BarsChart horizontal data={drift.data.features.slice(0, 15).map((f) => ({ f: f.feature, psi: f.psi }))} xKey="f" series={[{ key: "psi", label: "PSI" }]} height={Math.max(180, Math.min(15, drift.data.features.length) * 26)} fmt={(v) => v.toFixed(3)} />
              </Panel>}
            </div>
          )}
        </TabPanel>
        <TabPanel value="deployments" className="pt-4">
          <Panel flush>{d.deployments.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Jamais déployé.", "Never deployed.")}</p> : (
            <ul>{d.deployments.map((x, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5 text-sm last:border-0">
                <Badge tone={x.action === "rollback" ? "medium" : "accent"}>{x.action}</Badge><span>v{x.version}{x.previous_version ? ` (${L("remplace", "replaces")} v${x.previous_version})` : ""}</span>
                {x.active && <Badge tone="ok">{L("active", "active")}</Badge>}<span className="text-ink-2">{x.reason}</span><span className="ml-auto text-xs text-ink-3">{x.actor} · {fmtDate(x.at, locale, true)}</span>
              </li>))}</ul>)}
          </Panel>
        </TabPanel>
      </Tabs>
      <Dialog open={!!action} onOpenChange={(v) => !v && setAction(null)}
        title={action?.kind === "approved" ? L("Approuver la version", "Approve the version") : action?.kind === "rejected" ? L("Rejeter la version", "Reject the version") : action?.kind === "deploy" ? L("Déployer en production", "Deploy to production") : L("Retour arrière", "Rollback")}
        description={action?.kind === "approved" ? L("Principe des quatre yeux : la personne qui a entraîné le modèle ne peut pas l'approuver. Justification d'au moins 10 caractères.", "Four-eyes principle: the person who trained the model cannot approve it. Justification of at least 10 characters.") : L("Cette opération concerne toutes les banques servies par la plateforme et est tracée.", "This operation affects every bank served by the platform and is logged.")}
        footer={<><Button onClick={() => setAction(null)}>{L("Annuler", "Cancel")}</Button><Button variant={action?.kind === "rejected" ? "danger" : "primary"} disabled={comment.trim().length < (action?.kind === "approved" || action?.kind === "rejected" ? 10 : 5)} loading={act.isPending} onClick={() => act.mutate()}>{L("Confirmer", "Confirm")}</Button></>}>
        <Field label={L("Justification", "Justification")} htmlFor="mc"><Textarea id="mc" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
      </Dialog>
    </div>
  );
}
