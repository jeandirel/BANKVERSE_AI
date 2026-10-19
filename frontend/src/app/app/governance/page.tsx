"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorState, Ledger, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { ApiError, get, patch, post } from "@/lib/api";
import { fmtDate, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";

type Overview = { models: { total: number; in_production: number; pending_review: number; drift_alerts: string[] }; incidents_open: Record<string, number>;
  agent_actions_pending: number; fairness_credit: Record<string, { review_rate_gap: number; equal_opportunity_gap: number; calibration_gap_max: number }> | null;
  controls: { key: string; label: string; status: string; evidence: string }[]; notice: string };
type Incident = { id: string; title: string; category: string; severity: string; status: string; model_name: string | null; description: string; opened_by: string;
  created_at: string; resolved_at: string | null; resolution: string | null; detected_automatically: boolean };

const sevTone = (s: string) => (s === "critical" ? "critical" : s === "high" ? "high" : s === "medium" ? "medium" : "neutral") as "critical" | "high" | "medium" | "neutral";

export default function GovernancePage() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ title: "", category: "model_drift", severity: "medium", model_name: "", description: "" });
  const [resolve, setResolve] = useState<Incident | null>(null);
  const [resolution, setResolution] = useState("");
  const ov = useQuery({ queryKey: ["governance", "overview"], queryFn: () => get<Overview>("/governance/overview") });
  const inc = useQuery({ queryKey: ["governance", "incidents"], queryFn: () => get<Incident[]>("/governance/incidents") });
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const create = useMutation({ mutationFn: () => post("/governance/incidents", { ...form, model_name: form.model_name || null }),
    onSuccess: () => { setOpen(false); qc.invalidateQueries({ queryKey: ["governance"] }); toast(L("Incident déclaré", "Incident declared")); }, onError: onErr });
  const update = useMutation({ mutationFn: (v: { id: string; status: string; resolution?: string }) => patch(`/governance/incidents/${v.id}`, v),
    onSuccess: () => { setResolve(null); setResolution(""); qc.invalidateQueries({ queryKey: ["governance"] }); }, onError: onErr });
  const report = useMutation({ mutationFn: () => post<{ report_id: string }>("/governance/report"), onSuccess: () => { toast(L("Rapport généré", "Report generated")); router.push("/app/reports"); }, onError: onErr });
  const o = ov.data;
  const openInc = o ? Object.values(o.incidents_open).reduce((a, b) => a + b, 0) : 0;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.governance")} subtitle={L("Contrôles de gouvernance de l'IA, équité, incidents et rapport de supervision.", "AI governance controls, fairness, incidents and supervision report.")}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined}
        actions={<div className="flex gap-2">
          {can("reports.create") && <Button icon={<FileText className="size-4" />} loading={report.isPending} onClick={() => report.mutate()}>{L("Rapport PDF", "PDF report")}</Button>}
          {can("governance.manage") && <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setOpen(true)}>{L("Déclarer un incident", "Declare an incident")}</Button>}</div>} />
      {ov.error ? <ErrorState error={ov.error} /> : !o ? <LoadingBlock /> : (
        <>
          <Notice>{o.notice}</Notice>
          <Ledger items={[
            { label: L("Modèles en production", "Models in production"), value: `${o.models.in_production} / ${o.models.total}` },
            { label: L("Versions en revue", "Versions in review"), value: String(o.models.pending_review), tone: o.models.pending_review ? "medium" : undefined },
            { label: L("Dérives significatives", "Significant drifts"), value: String(o.models.drift_alerts.length), tone: o.models.drift_alerts.length ? "high" : "ok" },
            { label: L("Incidents ouverts", "Open incidents"), value: String(openInc), tone: openInc ? "high" : "ok" },
            { label: L("Actions d'agents en attente", "Pending agent actions"), value: String(o.agent_actions_pending) },
          ]} />
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,1fr)]">
            <Panel title={L("Contrôles", "Controls")} description={L("État constaté dans cette installation, avec la preuve associée.", "Observed state in this installation, with its evidence.")} flush>
              <ul>{o.controls.map((c) => (
                <li key={c.key} className="flex items-start gap-3 border-b border-line px-4 py-2.5 last:border-0">
                  <Badge tone={c.status === "enforced" ? "ok" : "high"} className="mt-0.5 shrink-0">{c.status === "enforced" ? L("En place", "In place") : L("Non réalisé", "Not done")}</Badge>
                  <div><p className="text-sm font-medium">{c.label}</p><p className="text-xs text-ink-3">{c.evidence}</p></div>
                </li>))}</ul>
            </Panel>
            <Panel title={L("Équité — modèle crédit en production", "Fairness — production credit model")} description={L("Écarts entre groupes sur le jeu de test (attributs exclus du modèle, suivis a posteriori).", "Gaps between groups on the test set (attributes excluded from the model, monitored afterwards).")}>
              {!o.fairness_credit ? <p className="text-sm text-ink-3">{L("Aucun modèle crédit en production.", "No credit model in production.")}</p> : (
                <table className="w-full text-sm"><thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3"><th className="py-1.5 font-medium">{L("Attribut", "Attribute")}</th><th className="px-2 font-medium">{L("Écart taux de revue", "Review-rate gap")}</th><th className="px-2 font-medium">{L("Égalité des chances", "Equal opportunity")}</th><th className="px-2 font-medium">{L("Calibration", "Calibration")}</th></tr></thead>
                  <tbody>{Object.entries(o.fairness_credit).map(([k, v]) => (
                    <tr key={k} className="border-b border-line"><td className="py-1.5">{k}</td><td className="px-2 num">{fmtPct(v.review_rate_gap, locale)}</td><td className="px-2 num">{fmtPct(v.equal_opportunity_gap, locale)}</td><td className="px-2 num">{fmtPct(v.calibration_gap_max, locale)}</td></tr>))}</tbody></table>
              )}
              <Link href="/app/models/credit_default_pd" className="mt-3 inline-block text-sm text-accent hover:underline">{L("Voir la fiche du modèle", "View the model card")}</Link>
            </Panel>
          </div>
        </>
      )}
      <Panel title={L("Incidents", "Incidents")} flush>
        {inc.error ? <ErrorState error={inc.error} /> : !inc.data ? <LoadingBlock /> : inc.data.length === 0 ? <p className="px-4 py-6 text-sm text-ink-3">{L("Aucun incident déclaré.", "No incident declared.")}</p> : (
          <ul>{inc.data.map((i) => (
            <li key={i.id} className="border-b border-line px-4 py-3 last:border-0">
              <div className="flex flex-wrap items-center gap-2"><Badge tone={sevTone(i.severity)}>{i.severity}</Badge><span className="font-medium">{i.title}</span>
                <Badge tone={i.status === "resolved" ? "ok" : "medium"}>{i.status}</Badge>{i.model_name && <Link className="text-xs text-accent hover:underline" href={`/app/models/${i.model_name}`}>{i.model_name}</Link>}
                <span className="ml-auto text-xs text-ink-3">{i.opened_by} · {fmtDate(i.created_at, locale, true)}</span></div>
              {i.description && <p className="mt-1 text-sm text-ink-2">{i.description}</p>}
              {i.resolution && <p className="mt-1 text-sm"><span className="text-ink-3">{L("Résolution :", "Resolution:")}</span> {i.resolution}</p>}
              {can("governance.manage") && i.status !== "resolved" && <div className="mt-2 flex gap-2">
                {i.status === "open" && <Button size="sm" onClick={() => update.mutate({ id: i.id, status: "mitigating" })}>{L("En traitement", "Mitigating")}</Button>}
                <Button size="sm" onClick={() => setResolve(i)}>{L("Résoudre", "Resolve")}</Button></div>}
            </li>))}</ul>
        )}
      </Panel>
      <Dialog open={open} onOpenChange={setOpen} title={L("Déclarer un incident", "Declare an incident")}
        footer={<><Button onClick={() => setOpen(false)}>{L("Annuler", "Cancel")}</Button><Button variant="primary" disabled={form.title.trim().length < 5} loading={create.isPending} onClick={() => create.mutate()}>{L("Déclarer", "Declare")}</Button></>}>
        <div className="flex flex-col gap-3">
          <Field label={L("Titre", "Title")} htmlFor="i-t"><Input id="i-t" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label={L("Catégorie", "Category")} htmlFor="i-c"><Select id="i-c" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {["model_drift", "model_error", "data_quality", "pipeline", "security", "other"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
            <Field label={L("Gravité", "Severity")} htmlFor="i-s"><Select id="i-s" value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
              {["low", "medium", "high", "critical"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          </div>
          <Field label={L("Modèle concerné (facultatif)", "Model concerned (optional)")} htmlFor="i-m"><Input id="i-m" value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} /></Field>
          <Field label={L("Description", "Description")} htmlFor="i-d"><Textarea id="i-d" rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        </div>
      </Dialog>
      <Dialog open={!!resolve} onOpenChange={(v) => !v && setResolve(null)} title={L("Résoudre l'incident", "Resolve the incident")} description={resolve?.title}
        footer={<><Button onClick={() => setResolve(null)}>{L("Annuler", "Cancel")}</Button><Button variant="primary" disabled={resolution.trim().length < 5} loading={update.isPending} onClick={() => resolve && update.mutate({ id: resolve.id, status: "resolved", resolution })}>{L("Résoudre", "Resolve")}</Button></>}>
        <Field label={L("Résolution", "Resolution")} htmlFor="i-r"><Textarea id="i-r" rows={3} value={resolution} onChange={(e) => setResolution(e.target.value)} /></Field>
      </Dialog>
    </div>
  );
}
