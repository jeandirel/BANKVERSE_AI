"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Bot, Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { DataTable } from "@/components/ui/data-table";
import { ErrorState, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, Tabs, TabPanel, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { ApiError, get, post, qs } from "@/lib/api";
import { AGENT_STATUS, agentStatusTone } from "@/lib/domain/agents";
import { fmtDate, fmtMoney } from "@/lib/format";
import { pick, useI18n, useL } from "@/lib/i18n";
import { useCan, useMe } from "@/lib/session";

type Agent = { key: string; label: { fr: string; en: string }; description: { fr: string; en: string }; entity_type: string | null; available: boolean };
type Catalogue = { agents: Agent[]; tools: { name: string; kind: string; permission: string; label: { fr: string; en: string }; allowed: boolean }[];
  limits: { max_steps: number; timeout_seconds: number }; planner: string; notice: string };
type RunRow = { id: string; agent: string | null; request: string; status: string; created_at: string; steps_used: number; pending_approvals: number; planner: string };
type Option = { id: string; label: string; hint?: string };

function useEntityOptions(type: string | null, q: string) {
  const { locale } = useI18n();
  return useQuery<Option[]>({
    queryKey: ["agents", "entities", type, q],
    enabled: !!type,
    queryFn: async () => {
      switch (type) {
        case "fraud_alert": {
          const r = await get<{ items: { id: string; score: number; risk_band: string; transaction: { merchant: string; amount: number }; customer: { name: string } }[] }>(`/fraud/alerts${qs({ status: "open", q, page_size: 30 })}`);
          return r.items.map((a) => ({ id: a.id, label: `${a.customer.name} — ${a.transaction.merchant}`, hint: `${a.risk_band} · ${a.score.toFixed(2)} · ${fmtMoney(a.transaction.amount, locale)}` }));
        }
        case "aml_alert": {
          const r = await get<{ items: { id: string; score: number; risk_band: string; typologies: string[]; customer: { name: string } }[] }>(`/aml/alerts${qs({ status: "open", q, page_size: 30 })}`);
          return r.items.map((a) => ({ id: a.id, label: a.customer.name, hint: `${a.risk_band} · ${a.typologies.join(", ")}` }));
        }
        case "credit_application": {
          const r = await get<{ items: { id: string; reference: string; grade: string | null; customer: { name: string } }[] }>(`/credit/applications${qs({ q, page_size: 30 })}`);
          return r.items.map((a) => ({ id: a.id, label: `${a.reference} — ${a.customer.name}`, hint: a.grade ? `grade ${a.grade}` : undefined }));
        }
        case "customer": {
          const r = await get<{ items: { id: string; name: string; ref: string; segment: string }[] }>(`/customers${qs({ q, page_size: 30 })}`);
          return r.items.map((c) => ({ id: c.id, label: c.name, hint: `${c.ref} · ${c.segment}` }));
        }
        case "case": {
          const r = await get<{ items: { id: string; reference: string; title: string; status: string }[] }>(`/cases${qs({ q, page_size: 30 })}`);
          return r.items.map((c) => ({ id: c.id, label: `${c.reference} — ${c.title}`, hint: c.status }));
        }
        case "series": {
          const r = await get<{ key: string; name: string }[]>("/forecasting/series");
          return r.map((s) => ({ id: s.key, label: s.name, hint: s.key }));
        }
        default:
          return [];
      }
    },
  });
}

function RunDialog({ agent, onOpenChange }: { agent: Agent | null; onOpenChange: (v: boolean) => void }) {
  const L = useL();
  const { locale } = useI18n();
  const router = useRouter();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [entity, setEntity] = useState("");
  const [horizon, setHorizon] = useState("12");
  const [request, setRequest] = useState("");
  const opts = useEntityOptions(agent?.entity_type ?? null, q);
  const run = useMutation({
    mutationFn: () => post<{ id: string }>("/agents/runs", {
      agent: agent?.key, request: request.trim() || pick(agent?.description, locale), entity_type: agent?.entity_type ?? undefined,
      entity_id: agent?.entity_type ? entity : undefined, params: agent?.entity_type === "series" ? { horizon: Number(horizon) } : {} }),
    onSuccess: (r) => router.push(`/app/agents/runs/${r.id}`),
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  if (!agent) return null;
  const needsEntity = !!agent.entity_type;
  return (
    <Dialog open onOpenChange={onOpenChange} title={pick(agent.label, locale)} description={pick(agent.description, locale)}
      footer={<><Button onClick={() => onOpenChange(false)}>{L("Annuler", "Cancel")}</Button>
        <Button variant="primary" icon={<Play className="size-4" />} disabled={needsEntity && !entity} loading={run.isPending} onClick={() => run.mutate()}>{L("Lancer l'agent", "Run the agent")}</Button></>}>
      <div className="flex flex-col gap-3">
        {needsEntity && <>
          {agent.entity_type !== "series" && <Field label={L("Rechercher", "Search")} htmlFor="ag-q"><Input id="ag-q" value={q} onChange={(e) => setQ(e.target.value)} /></Field>}
          <Field label={L("Objet à analyser", "Object to analyse")} htmlFor="ag-e">
            <Select id="ag-e" value={entity} onChange={(e) => setEntity(e.target.value)}>
              <option value="">{opts.isLoading ? L("Chargement…", "Loading…") : L("Choisir…", "Choose…")}</option>
              {opts.data?.map((o) => <option key={o.id} value={o.id}>{o.label}{o.hint ? ` · ${o.hint}` : ""}</option>)}
            </Select>
          </Field>
          {opts.error && <p className="text-sm text-risk-high">{opts.error instanceof ApiError ? opts.error.message : String(opts.error)}</p>}
        </>}
        {agent.entity_type === "series" && <Field label={L("Horizon (mois)", "Horizon (months)")} htmlFor="ag-h"><Input id="ag-h" type="number" min={1} max={24} value={horizon} onChange={(e) => setHorizon(e.target.value)} /></Field>}
        <Field label={L("Consigne (facultatif)", "Instruction (optional)")} htmlFor="ag-r"><Textarea id="ag-r" rows={3} value={request} onChange={(e) => setRequest(e.target.value)} placeholder={pick(agent.description, locale)} /></Field>
      </div>
    </Dialog>
  );
}

export default function AgentsPage() {
  const router = useRouter();
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const { data: me } = useMe();
  const [tab, setTab] = useState("agents");
  const [open, setOpen] = useState<Agent | null>(null);
  const cat = useQuery({ queryKey: ["agents", "catalogue"], queryFn: () => get<Catalogue>("/agents") });
  const runs = useQuery({ queryKey: ["agents", "runs", tab], queryFn: () => get<RunRow[]>(`/agents/runs${qs({ mine: tab !== "all" })}`), enabled: tab !== "agents" && tab !== "approvals" });
  const approvals = useQuery({ queryKey: ["agents", "approvals"], queryFn: () => get<{ id: string; run_id: string; agent: string; label: { fr: string; en: string }; rationale: string; created_at: string; allowed: boolean }[]>("/agents/approvals"), enabled: can("agents.approve") });
  const labels = useMemo(() => Object.fromEntries((cat.data?.agents ?? []).map((a) => [a.key, pick(a.label, locale)])), [cat.data, locale]);
  const cols = useMemo<ColumnDef<RunRow, unknown>[]>(() => [
    { accessorKey: "agent", header: L("Agent", "Agent"), cell: ({ getValue }) => labels[String(getValue())] ?? String(getValue() ?? "—") },
    { accessorKey: "request", header: L("Consigne", "Instruction"), cell: ({ getValue }) => <span className="line-clamp-1 max-w-[360px] text-ink-2">{String(getValue())}</span> },
    { accessorKey: "status", header: t("common.status"), cell: ({ row }) => <div className="flex gap-1.5"><Badge tone={agentStatusTone(row.original.status)}>{pick(AGENT_STATUS[row.original.status], locale, row.original.status)}</Badge>
      {row.original.pending_approvals > 0 && <Badge tone="medium">{row.original.pending_approvals} {L("à approuver", "to approve")}</Badge>}</div> },
    { accessorKey: "steps_used", header: L("Étapes", "Steps"), meta: { align: "right" } },
    { accessorKey: "created_at", header: L("Lancé", "Started"), cell: ({ getValue }) => fmtDate(String(getValue()), locale, true) },
  ], [L, t, labels, locale]);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.agents")} subtitle={L("Des agents spécialisés préparent l'analyse ; vous approuvez chaque action qu'ils proposent.", "Specialised agents prepare the analysis; you approve every action they propose.")}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined} />
      {cat.error ? <ErrorState error={cat.error} /> : !cat.data ? <LoadingBlock /> : <Notice>{cat.data.notice} {L(`Limites : ${cat.data.limits.max_steps} outils par exécution, ${cat.data.limits.timeout_seconds} s. Planification : ${cat.data.planner === "deterministic" ? "déterministe (aucun LLM configuré)" : cat.data.planner}.`, `Limits: ${cat.data.limits.max_steps} tools per run, ${cat.data.limits.timeout_seconds} s. Planner: ${cat.data.planner === "deterministic" ? "deterministic (no LLM configured)" : cat.data.planner}.`)}</Notice>}
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "agents", label: L("Agents", "Agents") }, { value: "mine", label: L("Mes exécutions", "My runs") },
        ...(can("agents.approve") ? [{ value: "approvals", label: L("Approbations", "Approvals"), count: approvals.data?.length }, { value: "all", label: L("Toutes les exécutions", "All runs") }] : [])]}>
        <TabPanel value="agents" className="pt-4">
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {cat.data?.agents.map((a) => (
              <li key={a.key} className="flex flex-col rounded-[var(--radius-panel)] border border-line bg-surface p-4">
                <div className="flex items-center gap-2"><Bot className="size-4 text-accent" aria-hidden /><h2 className="font-semibold text-[15px]">{pick(a.label, locale)}</h2></div>
                <p className="mt-2 flex-1 text-sm text-ink-2">{pick(a.description, locale)}</p>
                <Button className="mt-4 self-start" size="sm" disabled={!a.available} onClick={() => setOpen(a)} icon={<Play className="size-3.5" />}>
                  {a.available ? L("Lancer", "Run") : L("Non autorisé pour votre rôle", "Not allowed for your role")}</Button>
              </li>
            ))}
          </ul>
          {cat.data && <Panel className="mt-6" title={L("Outils disponibles", "Available tools")} description={L("Les outils d'action ne s'exécutent qu'après approbation humaine.", "Action tools only run after human approval.")} flush>
            <ul className="grid sm:grid-cols-2">{cat.data.tools.map((x) => (
              <li key={x.name} className="flex items-center gap-2 border-b border-line px-4 py-2 text-sm">
                <Badge tone={x.kind === "action" ? "medium" : "neutral"}>{x.kind === "action" ? L("Action", "Action") : L("Lecture", "Read")}</Badge>
                <span className={x.allowed ? "" : "text-ink-3"}>{pick(x.label, locale)}</span><span className="ml-auto text-xs text-ink-3">{x.permission}</span>
              </li>))}</ul>
          </Panel>}
        </TabPanel>
        {["mine", "all"].map((v) => (
          <TabPanel key={v} value={v} className="pt-4">
            <Panel flush>{runs.error ? <ErrorState error={runs.error} /> : <DataTable columns={cols} data={runs.data} loading={runs.isLoading} rowHref={(r) => `/app/agents/runs/${r.id}`} />}</Panel>
          </TabPanel>
        ))}
        <TabPanel value="approvals" className="pt-4">
          <Panel flush>
            {!approvals.data ? <LoadingBlock /> : approvals.data.length === 0 ? <p className="px-4 py-6 text-sm text-ink-3">{L("Aucune action en attente.", "No pending action.")}</p> : (
              <ul>{approvals.data.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-0">
                  <Badge tone="medium">{pick(a.label, locale)}</Badge>
                  <span className="text-sm text-ink-2 flex-1 min-w-[200px]">{a.rationale}</span>
                  <span className="text-xs text-ink-3">{labels[a.agent] ?? a.agent} · {fmtDate(a.created_at, locale, true)}</span>
                  <Button size="sm" onClick={() => router.push(`/app/agents/runs/${a.run_id}`)}>{L("Examiner", "Review")}</Button>
                </li>))}</ul>
            )}
          </Panel>
        </TabPanel>
      </Tabs>
      {open && <RunDialog agent={open} onOpenChange={(v) => !v && setOpen(null)} />}
    </div>
  );
}
