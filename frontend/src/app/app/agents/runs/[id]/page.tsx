"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ChevronDown, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Dialog, useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Textarea } from "@/components/ui/primitives";
import { ApiError, get, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { AGENT_STATUS, agentStatusTone } from "@/lib/domain/agents";
import { fmtDate } from "@/lib/format";
import { pick, useI18n, useL } from "@/lib/i18n";

type Step = { ordinal: number; tool: string | null; label: { fr: string; en: string } | null; input: Record<string, unknown> | null;
  output: Record<string, unknown> | null; status: string; duration_ms: number | null; note: string | null };
type Approval = { id: string; action: string; label: { fr: string; en: string } | null; input: Record<string, unknown>; rationale: string | null;
  status: string; decided_at: string | null; comment: string | null; can_decide: boolean };
type Run = { id: string; agent: string | null; agent_label: { fr: string; en: string } | null; request: string; status: string; planner: string;
  created_at: string; finished_at: string | null; steps_used: number; error: string | null;
  result: { narrative: string[]; sources: { n: number; title: string; scope: string; excerpt: string }[]; skipped: { tool: string; reason: string }[];
    message: string | null; outcome: string; duration_ms: number } | null;
  steps: Step[]; approvals: Approval[]; notice: string };

function StepRow({ s }: { s: Step }) {
  const { locale } = useI18n();
  const L = useL();
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-line last:border-0">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-surface-2">
        <span className="num w-6 text-ink-3">{s.ordinal}</span>
        <span className="flex-1">{s.label ? pick(s.label, locale) : s.tool}</span>
        {s.note && <Badge tone="medium">{L("Contenu filtré", "Content filtered")}</Badge>}
        <Badge tone={s.status === "ok" ? "ok" : "high"}>{s.status}</Badge>
        <span className="num w-16 text-right text-xs text-ink-3">{s.duration_ms ?? "—"} ms</span>
        <ChevronDown className={cn("size-4 text-ink-3 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && <div className="grid gap-3 px-4 pb-3 md:grid-cols-2">
        {s.note && <p className="md:col-span-2 text-xs text-risk-high">{s.note}</p>}
        <div><p className="text-xs text-ink-3 mb-1">{L("Entrée", "Input")}</p><pre className="max-h-64 overflow-auto rounded bg-surface-2 p-2 text-[11.5px] leading-snug">{JSON.stringify(s.input, null, 2)}</pre></div>
        <div><p className="text-xs text-ink-3 mb-1">{L("Sortie", "Output")}</p><pre className="max-h-64 overflow-auto rounded bg-surface-2 p-2 text-[11.5px] leading-snug">{JSON.stringify(s.output, null, 2)}</pre></div>
      </div>}
    </li>
  );
}

export default function AgentRunPage() {
  const { id } = useParams<{ id: string }>();
  const { locale } = useI18n();
  const L = useL();
  const qc = useQueryClient();
  const toast = useToast();
  const [decision, setDecision] = useState<{ a: Approval; d: "approved" | "rejected" } | null>(null);
  const [comment, setComment] = useState("");
  const run = useQuery({ queryKey: ["agents", "run", id], queryFn: () => get<Run>(`/agents/runs/${id}`) });
  const decide = useMutation({
    mutationFn: () => post<{ status: string; result: Record<string, unknown> | null }>(`/agents/approvals/${decision?.a.id}`, { decision: decision?.d, comment }),
    onSuccess: (r) => { setDecision(null); setComment(""); qc.invalidateQueries({ queryKey: ["agents"] });
      toast(r.status === "approved" ? L("Action approuvée et exécutée", "Action approved and executed") : L("Action rejetée", "Action rejected")); },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  if (run.error) return <ErrorState error={run.error} onRetry={() => run.refetch()} />;
  const r = run.data;
  if (!r) return <LoadingBlock />;
  const res = r.result;
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/agents" className="inline-flex items-center gap-1.5 text-sm text-ink-2 hover:text-ink w-fit"><ArrowLeft className="size-4" />{L("Agents", "Agents")}</Link>
      <PageHeader title={r.agent_label ? pick(r.agent_label, locale) : L("Exécution d'agent", "Agent run")} subtitle={r.request}
        meta={<Badge tone={agentStatusTone(res?.outcome === "needs_input" ? "needs_input" : r.status)}>{pick(AGENT_STATUS[res?.outcome === "needs_input" ? "needs_input" : r.status], locale, r.status)}</Badge>} />
      <Notice>{r.notice}</Notice>
      {res?.message && <Notice tone={r.status === "blocked" || r.status === "refused" || r.status === "failed" ? "warn" : "info"}>{res.message}</Notice>}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,1fr)]">
        <div className="flex flex-col gap-6 min-w-0">
          {res && res.narrative.length > 0 && <Panel title={L("Synthèse", "Summary")} description={L("Produite à partir des sorties d'outils ci-dessous ; à vérifier.", "Built from the tool outputs below; to be checked.")}>
            <div className="flex flex-col gap-2.5 text-[14.5px] leading-relaxed">{res.narrative.map((p, i) => <p key={i} className={p.startsWith("•") ? "pl-3 text-ink-2" : ""}>{p}</p>)}</div>
            {res.sources.length > 0 && <div className="mt-4 border-t border-line pt-3">
              <p className="text-xs text-ink-3 mb-1.5">{L("Sources citées", "Cited sources")}</p>
              <ol className="flex flex-col gap-1.5 text-[13px]">{res.sources.map((s) => <li key={s.n}><span className="num font-semibold">[{s.n}]</span> {s.title} — <span className="text-ink-2">{s.excerpt.slice(0, 220)}…</span></li>)}</ol>
            </div>}
          </Panel>}
          <Panel flush title={L("Trace d'exécution", "Execution trace")} description={L(`${r.steps.length} appel(s) d'outils · planification ${r.planner}`, `${r.steps.length} tool call(s) · planner ${r.planner}`)}>
            {r.steps.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Aucun outil appelé.", "No tool called.")}</p> : <ul>{r.steps.map((s) => <StepRow key={s.ordinal} s={s} />)}</ul>}
          </Panel>
        </div>
        <div className="flex flex-col gap-6">
          <Panel title={L("Actions proposées", "Proposed actions")} description={L("Rien n'est exécuté sans approbation ; l'action s'exécute avec les droits de l'approbateur.", "Nothing runs without approval; the action executes with the approver's permissions.")} flush>
            {r.approvals.length === 0 ? <p className="px-4 py-4 text-sm text-ink-3">{L("Aucune action proposée.", "No action proposed.")}</p> : (
              <ul>{r.approvals.map((a) => (
                <li key={a.id} className="border-b border-line px-4 py-3 last:border-0">
                  <div className="flex items-center gap-2"><span className="font-medium text-sm flex-1">{a.label ? pick(a.label, locale) : a.action}</span>
                    <Badge tone={a.status === "approved" ? "ok" : a.status === "rejected" ? "high" : "medium"}>{a.status === "pending" ? L("En attente", "Pending") : a.status === "approved" ? L("Approuvée", "Approved") : L("Rejetée", "Rejected")}</Badge></div>
                  {a.rationale && <p className="mt-1 text-sm text-ink-2">{a.rationale}</p>}
                  {a.comment && <p className="mt-1 text-xs text-ink-3">« {a.comment} » · {fmtDate(a.decided_at, locale, true)}</p>}
                  {a.can_decide && <div className="mt-2 flex gap-2">
                    <Button size="sm" icon={<X className="size-3.5" />} onClick={() => setDecision({ a, d: "rejected" })}>{L("Rejeter", "Reject")}</Button>
                    <Button size="sm" variant="primary" icon={<Check className="size-3.5" />} onClick={() => setDecision({ a, d: "approved" })}>{L("Approuver", "Approve")}</Button>
                  </div>}
                  {a.status === "pending" && !a.can_decide && <p className="mt-2 text-xs text-ink-3">{L("Approbation par un rôle habilité (responsable des risques, conformité).", "Approval by an authorised role (risk manager, compliance).")}</p>}
                </li>))}</ul>
            )}
          </Panel>
          <Panel title={L("Exécution", "Run")}>
            <KeyValue rows={[[L("Lancée", "Started"), fmtDate(r.created_at, locale, true)], [L("Terminée", "Finished"), r.finished_at ? fmtDate(r.finished_at, locale, true) : "—"],
              [L("Durée", "Duration"), res ? `${res.duration_ms} ms` : "—"], [L("Outils appelés", "Tools called"), String(r.steps_used)],
              [L("Éléments non consultés", "Skipped evidence"), res?.skipped.length ? res.skipped.map((s) => `${s.tool} (${s.reason})`).join(", ") : "—"],
              ...(r.error ? [[L("Erreur", "Error"), r.error] as [string, string]] : [])]} />
          </Panel>
        </div>
      </div>
      <Dialog open={!!decision} onOpenChange={(v) => !v && setDecision(null)}
        title={decision?.d === "approved" ? L("Approuver l'action", "Approve the action") : L("Rejeter l'action", "Reject the action")}
        description={decision?.a.label ? pick(decision.a.label, locale) : undefined}
        footer={<><Button onClick={() => setDecision(null)}>{L("Annuler", "Cancel")}</Button>
          <Button variant={decision?.d === "rejected" ? "danger" : "primary"} disabled={comment.trim().length < 3} loading={decide.isPending} onClick={() => decide.mutate()}>
            {decision?.d === "approved" ? L("Approuver et exécuter", "Approve and execute") : L("Rejeter", "Reject")}</Button></>}>
        {decision && <div className="flex flex-col gap-3">
          <pre className="max-h-48 overflow-auto rounded bg-surface-2 p-2 text-[11.5px]">{JSON.stringify(decision.a.input, null, 2)}</pre>
          <Field label={L("Commentaire (journal d'audit)", "Comment (audit log)")} htmlFor="ap-c"><Textarea id="ap-c" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        </div>}
      </Dialog>
    </div>
  );
}
