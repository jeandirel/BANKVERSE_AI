"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Briefcase, CheckCircle2, Flag, UserCheck, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { BarsChart } from "@/components/ui/charts";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Textarea } from "@/components/ui/primitives";
import { RiskBadge, RiskGauge, type Band } from "@/components/ui/risk";
import { ApiError, get, patch, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { statusLabel } from "@/lib/domain/fraud";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type Detail = {
  id: string; status: string; risk_band: Band; score: number; expected_loss: number; created_at: string; sla_due_at: string | null;
  assigned_to: string | null; analyst_label: string | null; case_id: string | null; model_version: string; resolution_note: string | null;
  reasons: { feature: string; label: string; value: number | string | null; contribution: number }[];
  transaction: { id: string; ts: string; amount: number; currency: string; merchant: string; merchant_city: string; mcc: number; channel: string; errors: string | null };
  customer: { id: string; name: string; ref: string; segment: string; is_demo: boolean };
  customer_profile: { city: string; segment: string; risk_rating: string; kyc_status: string; customer_since: string; pep: boolean };
  card: { id: string; ref: string; brand: string; type: string; status: string; home: string } | null;
  history: { id: string; ts: string; amount: number; merchant: string; city: string; mcc: number; channel: string; errors: string | null; risk_score: number | null; is_current: boolean }[];
  other_alerts_on_card: { id: string; status: string; score: number; created_at: string }[];
  simulation_label?: { scenario: string | null; is_fraud: boolean | null; note: string };
};

export default function FraudAlertPage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [note, setNote] = useState("");
  const q = useQuery({ queryKey: ["fraud", "alert", id], queryFn: () => get<Detail>(`/fraud/alerts/${id}`) });
  const update = useMutation({
    mutationFn: (body: { status?: string; assign_to_me?: boolean; note?: string }) => patch(`/fraud/alerts/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fraud"] });
      toast(L("Alerte mise à jour", "Alert updated"));
      setNote("");
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });
  const openCase = useMutation({
    mutationFn: () => post<{ case_id: string; reference: string }>(`/fraud/alerts/${id}/case`, {}),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["fraud"] });
      router.push(`/app/cases/${r.case_id}`);
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : String(e), "error"),
  });

  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const a = q.data;
  const closed = a.status.startsWith("closed");
  const tx = a.transaction;
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/fraud" className="inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink w-fit"><ArrowLeft className="size-3.5" />{t("nav.fraud")}</Link>
      <PageHeader
        title={`${fmtMoney(tx.amount, locale)} · ${tx.merchant}`}
        subtitle={`${t("fraud.alert.title")} ${a.id.slice(0, 8)} · ${a.customer.name} (${a.customer.ref}) · ${fmtDate(tx.ts, locale, true)}`}
        meta={<><RiskBadge band={a.risk_band} /><Badge>{statusLabel(t, a.status)}</Badge>{a.case_id && <Link href={`/app/cases/${a.case_id}`}><Badge tone="accent"><Briefcase className="size-3" />{L("Dossier lié", "Linked case")}</Badge></Link>}{a.customer.is_demo && <Badge tone="demo">DEMO</Badge>}</>}
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="flex flex-col gap-6">
          <Panel title={t("fraud.alert.why")} description={t("fraud.alert.why.help")}>
            <div className="flex items-start gap-5">
              <div className="flex flex-col items-center gap-1.5 pt-1">
                <RiskGauge score={a.score} band={a.risk_band} />
                <span className="num text-2xl font-semibold">{a.score.toFixed(3)}</span>
                <span className="text-xs text-ink-3">{L("probabilité calibrée", "calibrated probability")}</span>
              </div>
              <div className="flex-1 min-w-0">
                {a.reasons.length === 0 ? <p className="text-sm text-ink-3">{L("Aucune contribution disponible pour ce modèle.", "No contributions available for this model.")}</p> : (
                  <BarsChart horizontal height={Math.max(160, a.reasons.length * 42)}
                    data={a.reasons.map((r) => ({ factor: `${r.label} = ${typeof r.value === "number" ? fmtNumber(r.value, locale, 2) : r.value ?? "—"}`, contribution: r.contribution }))}
                    xKey="factor" series={[{ key: "contribution", label: L("Contribution", "Contribution"), color: "var(--risk-high)" }]} fmt={(v) => v.toFixed(2)} />
                )}
                <p className="mt-2 text-xs text-ink-3">{t("common.model")} {a.model_version} · {L("perte attendue", "expected loss")} {fmtMoney(a.expected_loss, locale)}</p>
              </div>
            </div>
          </Panel>

          <Panel title={t("fraud.alert.history")} flush description={L("Les variables du modèle sont calculées uniquement à partir des transactions antérieures.", "Model features only use earlier transactions.")}>
            <div className="max-h-[420px] overflow-auto scroll-thin">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-surface"><tr className="border-b border-line text-left text-[12.5px] text-ink-3">
                  <th className="px-3 py-2 font-medium">{t("common.date")}</th><th className="px-3 py-2 font-medium">{t("fraud.col.merchant")}</th>
                  <th className="px-3 py-2 font-medium">{L("Canal", "Channel")}</th><th className="px-3 py-2 font-medium text-right">{t("common.amount")}</th>
                  <th className="px-3 py-2 font-medium text-right">{t("common.score")}</th></tr></thead>
                <tbody>
                  {a.history.map((h) => (
                    <tr key={h.id} className={cn("border-b border-line", h.is_current && "bg-risk-high-soft")}>
                      <td className="px-3 py-1.5 whitespace-nowrap text-ink-2">{fmtDate(h.ts, locale, true)}</td>
                      <td className="px-3 py-1.5"><span className="block truncate max-w-[260px]">{h.merchant}</span><span className="text-xs text-ink-3">MCC {h.mcc} · {h.city}{h.errors ? ` · ${h.errors}` : ""}</span></td>
                      <td className="px-3 py-1.5 text-ink-2 text-[13px]">{h.channel.replace(" Transaction", "")}</td>
                      <td className="px-3 py-1.5 text-right num">{fmtMoney(h.amount, locale)}</td>
                      <td className="px-3 py-1.5 text-right num">{h.risk_score == null ? "—" : h.risk_score.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>

        <div className="flex flex-col gap-6">
          {can("fraud.triage") && (
            <Panel title={t("fraud.alert.decide")}>
              {closed ? (
                <div className="text-sm">
                  <p className="text-ink">{statusLabel(t, a.status)}</p>
                  {a.resolution_note && <p className="mt-1 text-ink-2">{a.resolution_note}</p>}
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" icon={<UserCheck className="size-4" />} onClick={() => update.mutate({ assign_to_me: true, status: "in_review" })} loading={update.isPending}>{t("fraud.alert.take")}</Button>
                    <Button size="sm" icon={<Flag className="size-4" />} onClick={() => update.mutate({ status: "escalated", note: note || undefined })}>{t("fraud.alert.escalate")}</Button>
                  </div>
                  <Field label={t("fraud.alert.noteRequired")} htmlFor="note">
                    <Textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder={L("Ex. : client joint par téléphone, achat non reconnu, carte en opposition.", "E.g. customer reached by phone, purchase not recognised, card blocked.")} />
                  </Field>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="danger" icon={<XCircle className="size-4" />} disabled={note.trim().length < 10} onClick={() => update.mutate({ status: "closed_fraud", note })}>{t("fraud.alert.confirm")}</Button>
                    <Button size="sm" icon={<CheckCircle2 className="size-4" />} disabled={note.trim().length < 10} onClick={() => update.mutate({ status: "closed_legitimate", note })}>{t("fraud.alert.legit")}</Button>
                  </div>
                </div>
              )}
              {can("cases.write") && !a.case_id && (
                <Button className="mt-4 w-full" variant="primary" icon={<Briefcase className="size-4" />} onClick={() => openCase.mutate()} loading={openCase.isPending}>{t("fraud.alert.case")}</Button>
              )}
            </Panel>
          )}
          <Panel title={L("Transaction", "Transaction")}>
            <KeyValue rows={[
              [t("common.amount"), <span key="a" className="num">{fmtMoney(tx.amount, locale)}</span>],
              [L("Canal", "Channel"), tx.channel],
              [t("fraud.col.merchant"), tx.merchant],
              [L("Lieu", "Location"), tx.merchant_city],
              ["MCC", tx.mcc],
              [L("Erreur d'autorisation", "Authorization error"), tx.errors ?? "—"],
            ]} />
          </Panel>
          <Panel title={t("common.customer")}>
            <KeyValue rows={[
              [L("Nom", "Name"), a.customer.name], [L("Référence", "Reference"), a.customer.ref],
              [L("Segment", "Segment"), a.customer_profile.segment], [L("Ville", "City"), a.customer_profile.city],
              [L("Notation risque", "Risk rating"), a.customer_profile.risk_rating], ["KYC", a.customer_profile.kyc_status],
              [L("Carte", "Card"), a.card ? `${a.card.ref} · ${a.card.brand} ${a.card.type}` : "—"],
            ]} />
          </Panel>
          {a.other_alerts_on_card.length > 0 && (
            <Panel title={L("Autres alertes sur la carte", "Other alerts on the card")}>
              <ul className="flex flex-col gap-1.5 text-sm">
                {a.other_alerts_on_card.map((o) => (
                  <li key={o.id}><Link className="flex justify-between hover:text-accent" href={`/app/fraud/alerts/${o.id}`}>
                    <span className="num">{o.score.toFixed(3)}</span><span className="text-ink-3">{statusLabel(t, o.status)}</span></Link></li>
                ))}
              </ul>
            </Panel>
          )}
          {a.simulation_label && (
            <Notice tone="demo" title={t("fraud.demoSim")}>
              {a.simulation_label.is_fraud ? L(`Scénario simulé : ${a.simulation_label.scenario}`, `Simulated scenario: ${a.simulation_label.scenario}`) : L("Transaction légitime dans la simulation.", "Legitimate transaction in the simulation.")} {a.simulation_label.note}
            </Notice>
          )}
        </div>
      </div>
    </div>
  );
}
