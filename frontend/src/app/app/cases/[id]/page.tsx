"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileDown, FileText, Gavel, MessageSquarePlus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Select, Textarea } from "@/components/ui/primitives";
import { RiskBadge, type Band } from "@/components/ui/risk";
import { ApiError, download, get, patch, post } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type CaseDetail = {
  id: string; reference: string; type: string; title: string; status: string; priority: string; created_at: string; summary: string | null;
  decision: string | null; decision_rationale: string | null; decided_at: string | null;
  customer: null | { id: string; name: string; ref: string; segment: string; risk_rating: string; kyc_status: string; is_demo: boolean };
  events: { id: string; kind: string; actor: string; at: string; payload: Record<string, unknown> }[];
  fraud_alerts: { id: string; score: number; band: Band; status: string; amount: number; merchant: string; ts: string }[];
  aml_alerts: { id: string; score: number; band: Band; status: string; typologies: string[]; iban: string }[];
  reports: { id: string; title: string; created_at: string; size: number }[];
};

const DECISIONS: Record<string, { value: string; fr: string; en: string }[]> = {
  fraud: [
    { value: "confirmed_fraud_card_blocked", fr: "Fraude confirmée — carte mise en opposition", en: "Confirmed fraud — card blocked" },
    { value: "confirmed_fraud_customer_contacted", fr: "Fraude confirmée — client contacté", en: "Confirmed fraud — customer contacted" },
    { value: "false_positive", fr: "Faux positif", en: "False positive" },
    { value: "inconclusive", fr: "Non concluant", en: "Inconclusive" },
  ],
  aml: [
    { value: "no_suspicion_closed", fr: "Absence de soupçon — clôture", en: "No suspicion — closed" },
    { value: "enhanced_monitoring", fr: "Surveillance renforcée", en: "Enhanced monitoring" },
    { value: "sar_filed_by_compliance", fr: "Déclaration de soupçon transmise par la conformité", en: "Suspicious activity report filed by compliance" },
    { value: "relationship_exit_review", fr: "Revue de la relation d'affaires", en: "Business relationship review" },
  ],
  credit: [
    { value: "approve", fr: "Accord", en: "Approve" }, { value: "decline", fr: "Refus", en: "Decline" },
    { value: "request_information", fr: "Demande de pièces", en: "Request information" },
  ],
  kyc: [
    { value: "documents_validated", fr: "Pièces validées", en: "Documents validated" }, { value: "documents_rejected", fr: "Pièces rejetées", en: "Documents rejected" },
    { value: "request_information", fr: "Demande de pièces", en: "Request information" },
  ],
};

function eventText(e: CaseDetail["events"][number], L: (fr: string, en: string) => string): string {
  const p = e.payload;
  switch (e.kind) {
    case "note": case "evidence": return String(p.text ?? "");
    case "status_change": return L(`Statut : ${p.from ?? "—"} → ${p.to}`, `Status: ${p.from ?? "—"} → ${p.to}`);
    case "link_alert": return L(`Alerte ${String(p.alert_type)} liée (score ${Number(p.score).toFixed(3)})`, `${String(p.alert_type)} alert linked (score ${Number(p.score).toFixed(3)})`);
    case "report": return L(`Rapport généré (SHA-256 ${String(p.sha256).slice(0, 12)}…)`, `Report generated (SHA-256 ${String(p.sha256).slice(0, 12)}…)`);
    case "decision": return `${String(p.decision)} — ${String(p.rationale)}`;
    case "agent": return String(p.summary ?? p.text ?? "");
    default: return JSON.stringify(p);
  }
}

export default function CasePage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [kind, setKind] = useState<"note" | "evidence">("note");
  const [decision, setDecision] = useState("");
  const [rationale, setRationale] = useState("");
  const q = useQuery({ queryKey: ["case", id], queryFn: () => get<CaseDetail>(`/cases/${id}`) });
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const refresh = () => qc.invalidateQueries({ queryKey: ["case", id] });
  const addNote = useMutation({ mutationFn: () => post(`/cases/${id}/events`, { text: note, kind }), onSuccess: () => { setNote(""); refresh(); }, onError: onErr });
  const setStatus = useMutation({ mutationFn: (status: string) => patch(`/cases/${id}`, { status }), onSuccess: refresh, onError: onErr });
  const decide = useMutation({ mutationFn: () => post(`/cases/${id}/decision`, { decision, rationale }), onSuccess: () => { refresh(); toast(L("Décision enregistrée", "Decision recorded")); }, onError: onErr });
  const report = useMutation({ mutationFn: () => post<{ report_id: string }>(`/cases/${id}/report`), onSuccess: () => { refresh(); toast(L("Rapport généré", "Report generated")); }, onError: onErr });

  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const c = q.data;
  const closed = c.status === "closed";
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/cases" className="inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink w-fit"><ArrowLeft className="size-3.5" />{t("nav.cases")}</Link>
      <PageHeader title={c.title} subtitle={`${c.reference} · ${L("ouvert le", "opened")} ${fmtDate(c.created_at, locale, true)}`}
        meta={<><Badge tone="accent">{c.type.toUpperCase()}</Badge><Badge>{c.status}</Badge><Badge tone={c.priority === "critical" ? "critical" : c.priority === "high" ? "high" : "neutral"}>{c.priority}</Badge>{c.customer?.is_demo && <Badge tone="demo">DEMO</Badge>}</>}
        actions={can("reports.create") ? <Button icon={<FileText className="size-4" />} onClick={() => report.mutate()} loading={report.isPending}>{L("Générer le rapport PDF", "Generate PDF report")}</Button> : undefined} />

      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <div className="flex flex-col gap-6">
          {(c.fraud_alerts.length > 0 || c.aml_alerts.length > 0) && (
            <Panel title={L("Alertes liées", "Linked alerts")} flush>
              <ul className="divide-y divide-line">
                {c.fraud_alerts.map((a) => (
                  <li key={a.id}><Link href={`/app/fraud/alerts/${a.id}`} className="flex items-center gap-4 px-4 py-2.5 hover:bg-surface-2">
                    <RiskBadge band={a.band} /><span className="num">{a.score.toFixed(3)}</span><span className="flex-1 truncate">{a.merchant}</span>
                    <span className="num">{fmtMoney(a.amount, locale)}</span><span className="text-xs text-ink-3">{fmtDate(a.ts, locale, true)}</span></Link></li>
                ))}
                {c.aml_alerts.map((a) => (
                  <li key={a.id}><Link href={`/app/aml/alerts/${a.id}`} className="flex items-center gap-4 px-4 py-2.5 hover:bg-surface-2">
                    <RiskBadge band={a.band} /><span className="num">{a.score.toFixed(3)}</span><span className="flex-1 truncate">{a.typologies.join(", ") || "—"}</span>
                    <span className="num text-xs text-ink-3">{a.iban}</span></Link></li>
                ))}
              </ul>
            </Panel>
          )}
          <Panel title={L("Chronologie de l'investigation", "Investigation timeline")}>
            <ol className="relative border-l border-line ml-1.5">
              {c.events.map((e) => (
                <li key={e.id} className="ml-4 pb-4 last:pb-0">
                  <span className="absolute -left-[5px] mt-1.5 size-2.5 rounded-full border-2 border-surface bg-ink-3" aria-hidden />
                  <p className="text-xs text-ink-3">{fmtDate(e.at, locale, true)} · {e.actor} · {e.kind}</p>
                  <p className="text-sm text-ink mt-0.5 whitespace-pre-wrap">{eventText(e, L)}</p>
                </li>
              ))}
            </ol>
            {can("cases.write") && !closed && (
              <div className="mt-5 flex flex-col gap-2 border-t border-line pt-4">
                <div className="flex gap-2">
                  <Select value={kind} onChange={(e) => setKind(e.target.value as "note" | "evidence")} className="w-40" aria-label={L("Type", "Type")}>
                    <option value="note">{t("common.note")}</option><option value="evidence">{L("Élément de preuve", "Evidence")}</option>
                  </Select>
                </div>
                <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder={L("Constatations, échanges avec le client, pièces consultées…", "Findings, customer contacts, documents reviewed…")} aria-label={t("common.note")} />
                <Button className="self-start" icon={<MessageSquarePlus className="size-4" />} disabled={note.trim().length < 2} loading={addNote.isPending} onClick={() => addNote.mutate()}>{L("Ajouter à la chronologie", "Add to timeline")}</Button>
              </div>
            )}
          </Panel>
        </div>
        <div className="flex flex-col gap-6">
          {c.customer && (
            <Panel title={t("common.customer")}>
              <KeyValue rows={[[L("Nom", "Name"), c.customer.name], [L("Référence", "Reference"), c.customer.ref], [L("Segment", "Segment"), c.customer.segment],
                [L("Notation risque", "Risk rating"), c.customer.risk_rating], ["KYC", c.customer.kyc_status]]} />
            </Panel>
          )}
          <Panel title={L("Statut et décision", "Status and decision")}>
            {closed ? (
              <div className="text-sm">
                <p className="font-medium text-ink">{DECISIONS[c.type]?.find((d) => d.value === c.decision)?.[locale] ?? c.decision}</p>
                <p className="mt-1 text-ink-2 whitespace-pre-wrap">{c.decision_rationale}</p>
                <p className="mt-2 text-xs text-ink-3">{fmtDate(c.decided_at, locale, true)}</p>
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {can("cases.write") && (
                  <div className="flex flex-wrap gap-2">
                    {["investigating", "pending_decision"].filter((s) => s !== c.status).map((s) => (
                      <Button key={s} size="sm" onClick={() => setStatus.mutate(s)}>{s === "investigating" ? L("Passer en investigation", "Mark investigating") : L("Soumettre pour décision", "Submit for decision")}</Button>
                    ))}
                  </div>
                )}
                {can("cases.decide") ? (
                  <>
                    <Field label={L("Décision", "Decision")} htmlFor="dec">
                      <Select id="dec" value={decision} onChange={(e) => setDecision(e.target.value)}>
                        <option value="">{L("Choisir…", "Choose…")}</option>
                        {(DECISIONS[c.type] ?? []).map((d) => <option key={d.value} value={d.value}>{d[locale]}</option>)}
                      </Select>
                    </Field>
                    <Field label={L("Motivation (20 caractères minimum)", "Rationale (at least 20 characters)")} htmlFor="rat">
                      <Textarea id="rat" value={rationale} onChange={(e) => setRationale(e.target.value)} />
                    </Field>
                    <Button variant="primary" icon={<Gavel className="size-4" />} disabled={!decision || rationale.trim().length < 20} loading={decide.isPending} onClick={() => decide.mutate()}>
                      {L("Enregistrer la décision", "Record decision")}
                    </Button>
                    {c.type === "aml" && <Notice>{L("Principe des quatre yeux : le créateur du dossier ne peut pas prendre la décision finale LCB-FT.", "Four-eyes principle: the case creator cannot take the final AML decision.")}</Notice>}
                  </>
                ) : <p className="text-sm text-ink-3">{L("La décision finale revient à un responsable des risques ou de la conformité.", "The final decision belongs to a risk or compliance manager.")}</p>}
              </div>
            )}
          </Panel>
          <Panel title={L("Rapports", "Reports")}>
            {c.reports.length === 0 ? <p className="text-sm text-ink-3">{L("Aucun rapport généré.", "No report generated.")}</p> : (
              <ul className="flex flex-col gap-2">
                {c.reports.map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate">{r.title} <span className="text-xs text-ink-3">{fmtDate(r.created_at, locale, true)}</span></span>
                    <Button size="sm" variant="ghost" icon={<FileDown className="size-4" />} onClick={() => download(`/api/v1/reports/${r.id}/download`, `${r.title}.pdf`)}>{t("common.download")}</Button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
