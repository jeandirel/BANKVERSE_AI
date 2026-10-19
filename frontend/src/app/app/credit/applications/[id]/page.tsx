"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Briefcase, Gavel, RefreshCcw, Sparkles } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BarsChart } from "@/components/ui/charts";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { RiskGauge } from "@/components/ui/risk";
import { ApiError, get, post } from "@/lib/api";
import { gradeTone } from "@/lib/domain/credit";
import { fmtDate, fmtMoney, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type App = {
  id: string; reference: string; status: string; product: string; requested_limit: number; pd: number | null; grade: string | null;
  features: { limit_bal: number; pay_status: number[]; bill_amt: number[]; pay_amt: number[]; age?: number; annual_income?: number };
  explanation: { feature: string; label: string; value: number; contribution: number; direction: string }[];
  model_version: string | null; scored_at: string | null; decision: string | null; rationale: string | null; decided_at: string | null;
  grade_bounds: Record<string, string>;
  customer: { id: string; name: string; ref: string; segment: string; occupation: string | null; annual_income: number | null; city: string; customer_since: string; is_demo: boolean } | null;
};
type WhatIfResp = { baseline: { pd: number; grade: string }; scenario: { pd: number; grade: string; factors: App["explanation"] }; delta_pd: number };

const PAY_LABEL: Record<number, { fr: string; en: string }> = {
  [-2]: { fr: "Pas d'utilisation", en: "No consumption" }, [-1]: { fr: "Payé à échéance", en: "Paid duly" }, 0: { fr: "Crédit renouvelable", en: "Revolving" },
};

export default function CreditApplicationPage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const q = useQuery({ queryKey: ["credit", "app", id], queryFn: () => get<App>(`/credit/applications/${id}`) });
  const [scen, setScen] = useState<{ limit_bal: string; pay_status: string[]; bill: string; paid: string } | null>(null);
  const [decision, setDecision] = useState("");
  const [rationale, setRationale] = useState("");
  useEffect(() => {
    if (q.data && !scen) {
      const f = q.data.features;
      setScen({ limit_bal: String(f.limit_bal), pay_status: f.pay_status.map(String), bill: String(Math.round(f.bill_amt[0])), paid: String(Math.round(f.pay_amt[0])) });
    }
  }, [q.data, scen]);
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const whatIf = useMutation({
    mutationFn: () => post<WhatIfResp>(`/credit/applications/${id}/what-if`, {
      limit_bal: Number(scen!.limit_bal), pay_status: scen!.pay_status.map(Number),
      bill_amt: Array(6).fill(Number(scen!.bill)), pay_amt: Array(6).fill(Number(scen!.paid)),
    }), onError: onErr,
  });
  const rescore = useMutation({ mutationFn: () => post(`/credit/applications/${id}/score`), onSuccess: () => qc.invalidateQueries({ queryKey: ["credit"] }), onError: onErr });
  const decide = useMutation({ mutationFn: () => post(`/credit/applications/${id}/decision`, { decision, rationale }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["credit"] }); toast(L("Décision de l'analyste enregistrée", "Analyst decision recorded")); }, onError: onErr });
  const mkCase = useMutation({ mutationFn: () => post<{ case_id: string }>(`/credit/applications/${id}/case`), onSuccess: (r) => router.push(`/app/cases/${r.case_id}`), onError: onErr });

  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const a = q.data;
  const f = a.features;
  const band = a.grade === "E" ? "critical" : a.grade === "D" ? "high" : a.grade === "C" ? "medium" : "low";
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/credit" className="inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink w-fit"><ArrowLeft className="size-3.5" />{t("nav.credit")}</Link>
      <PageHeader title={`${a.reference} · ${a.customer?.name ?? ""}`} subtitle={`${L("Plafond demandé", "Requested limit")} ${fmtMoney(a.requested_limit, locale, "EUR", 0)} · ${a.product}`}
        meta={<>{a.grade && <Badge tone={gradeTone(a.grade)}>Grade {a.grade}</Badge>}<Badge>{a.status}</Badge>{a.customer?.is_demo && <Badge tone="demo">DEMO</Badge>}</>}
        actions={<>{can("credit.assess") && <Button icon={<RefreshCcw className="size-4" />} onClick={() => rescore.mutate()} loading={rescore.isPending}>{L("Rescorer", "Re-score")}</Button>}
          {can("cases.write") && <Button icon={<Briefcase className="size-4" />} onClick={() => mkCase.mutate()} loading={mkCase.isPending}>{L("Ouvrir un dossier de revue", "Open a review case")}</Button>}</>} />
      <Notice>{L("Le score est une aide à la décision. Aucune demande n'est accordée ou refusée automatiquement ; la décision et sa motivation sont enregistrées par l'analyste.", "The score is decision support. No application is approved or declined automatically; the decision and its rationale are recorded by the analyst.")}</Notice>
      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <div className="flex flex-col gap-6">
          <Panel title={L("Probabilité de défaut et facteurs", "Probability of default and factors")} description={L("Contributions TreeSHAP en log-odds : positives = augmentent le risque.", "TreeSHAP contributions in log-odds: positive = increase risk.")}>
            <div className="flex items-start gap-5">
              <div className="flex flex-col items-center gap-1.5 pt-1">
                <RiskGauge score={a.pd} band={band as "low"} />
                <span className="num text-2xl font-semibold">{a.pd == null ? "—" : fmtPct(a.pd, locale)}</span>
                <span className="text-xs text-ink-3">PD 1 {L("mois", "month")}</span>
              </div>
              <div className="flex-1 min-w-0">
                <BarsChart horizontal height={Math.max(180, a.explanation.length * 40)} data={a.explanation.map((x) => ({ f: x.label, c: x.contribution }))} xKey="f"
                  series={[{ key: "c", label: L("Contribution", "Contribution") }]} fmt={(v) => v.toFixed(2)}
                  colorBy={(r) => (Number(r.c) > 0 ? "var(--risk-high)" : "var(--series-2)")} />
                <p className="mt-2 text-xs text-ink-3">{a.model_version} · {fmtDate(a.scored_at, locale, true)}</p>
              </div>
            </div>
          </Panel>
          <Panel title={L("Historique de paiement (6 mois, M-1 en premier)", "Payment history (6 months, M-1 first)")} flush>
            <table className="w-full text-sm">
              <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3"><th className="px-3 py-2 font-medium">{L("Mois", "Month")}</th><th className="px-3 py-2 font-medium">{L("Statut", "Status")}</th><th className="px-3 py-2 font-medium text-right">{L("Encours", "Balance")}</th><th className="px-3 py-2 font-medium text-right">{L("Remboursé", "Repaid")}</th></tr></thead>
              <tbody>{f.pay_status.map((s, i) => (
                <tr key={i} className="border-b border-line"><td className="px-3 py-1.5">M-{i + 1}</td>
                  <td className="px-3 py-1.5">{s >= 1 ? <Badge tone={s >= 2 ? "high" : "medium"}>{L(`${s} mois de retard`, `${s} month(s) late`)}</Badge> : PAY_LABEL[s]?.[locale]}</td>
                  <td className="px-3 py-1.5 text-right num">{fmtMoney(f.bill_amt[i], locale)}</td><td className="px-3 py-1.5 text-right num">{fmtMoney(f.pay_amt[i], locale)}</td></tr>))}</tbody>
            </table>
          </Panel>
          {scen && (
            <Panel title={L("Simulation de scénario", "Scenario simulation")} description={L("Modifiez le comportement de paiement pour mesurer l'effet sur la PD (non enregistré).", "Change payment behaviour to measure the effect on PD (not saved).")}>
              <div className="grid gap-3 md:grid-cols-3">
                <Field label={L("Plafond (EUR)", "Limit (EUR)")} htmlFor="sl"><Input id="sl" type="number" value={scen.limit_bal} onChange={(e) => setScen({ ...scen, limit_bal: e.target.value })} /></Field>
                <Field label={L("Encours mensuel (EUR)", "Monthly balance (EUR)")} htmlFor="sb"><Input id="sb" type="number" value={scen.bill} onChange={(e) => setScen({ ...scen, bill: e.target.value })} /></Field>
                <Field label={L("Remboursement mensuel (EUR)", "Monthly repayment (EUR)")} htmlFor="sp"><Input id="sp" type="number" value={scen.paid} onChange={(e) => setScen({ ...scen, paid: e.target.value })} /></Field>
              </div>
              <Field className="mt-3" label={L("Statuts de paiement M-1 … M-6", "Payment status M-1 … M-6")}>
                <div className="grid grid-cols-6 gap-2">{scen.pay_status.map((v, i) => (
                  <Select key={i} aria-label={`M-${i + 1}`} value={v} onChange={(e) => setScen({ ...scen, pay_status: scen.pay_status.map((x, j) => (j === i ? e.target.value : x)) })}>
                    {[-2, -1, 0, 1, 2, 3, 4, 5, 6].map((o) => <option key={o} value={o}>{o}</option>)}
                  </Select>))}</div>
              </Field>
              <Button className="mt-4" icon={<Sparkles className="size-4" />} onClick={() => whatIf.mutate()} loading={whatIf.isPending}>{L("Calculer le scénario", "Compute scenario")}</Button>
              {whatIf.data && (
                <div className="mt-4 rounded border border-line p-3 text-sm">
                  <p>{L("PD de référence", "Baseline PD")} <span className="num font-medium">{fmtPct(whatIf.data.baseline.pd, locale)}</span> ({whatIf.data.baseline.grade}) → {L("scénario", "scenario")} <span className="num font-medium">{fmtPct(whatIf.data.scenario.pd, locale)}</span> ({whatIf.data.scenario.grade}) · Δ <span className={`num font-medium ${whatIf.data.delta_pd > 0 ? "text-risk-high" : "text-ok"}`}>{whatIf.data.delta_pd > 0 ? "+" : ""}{(whatIf.data.delta_pd * 100).toFixed(1)} pts</span></p>
                </div>
              )}
            </Panel>
          )}
        </div>
        <div className="flex flex-col gap-6">
          <Panel title={L("Décision de l'analyste", "Analyst decision")}>
            {a.decision ? (
              <div className="text-sm"><p className="font-medium">{a.decision}</p><p className="mt-1 text-ink-2 whitespace-pre-wrap">{a.rationale}</p><p className="mt-2 text-xs text-ink-3">{fmtDate(a.decided_at, locale, true)}</p></div>
            ) : can("credit.assess") ? (
              <div className="flex flex-col gap-3">
                <Field label={L("Décision", "Decision")} htmlFor="d">
                  <Select id="d" value={decision} onChange={(e) => setDecision(e.target.value)}>
                    <option value="">{L("Choisir…", "Choose…")}</option><option value="approve">{L("Accord", "Approve")}</option><option value="decline">{L("Refus", "Decline")}</option><option value="request_information">{L("Demande de pièces", "Request information")}</option>
                  </Select>
                </Field>
                <Field label={L("Motivation (30 caractères minimum)", "Rationale (at least 30 characters)")} htmlFor="r"><Textarea id="r" value={rationale} onChange={(e) => setRationale(e.target.value)} /></Field>
                <Button variant="primary" icon={<Gavel className="size-4" />} disabled={!decision || rationale.trim().length < 30} loading={decide.isPending} onClick={() => decide.mutate()}>{L("Enregistrer la décision", "Record decision")}</Button>
              </div>
            ) : <p className="text-sm text-ink-3">{L("Lecture seule.", "Read-only.")}</p>}
          </Panel>
          {a.customer && <Panel title={t("common.customer")}>
            <KeyValue rows={[[L("Nom", "Name"), a.customer.name], [L("Référence", "Reference"), a.customer.ref], [L("Segment", "Segment"), a.customer.segment],
              [L("Profession", "Occupation"), a.customer.occupation ?? "—"], [L("Revenu annuel", "Annual income"), a.customer.annual_income ? fmtMoney(a.customer.annual_income, locale, "EUR", 0) : "—"],
              [L("Client depuis", "Customer since"), fmtDate(a.customer.customer_since, locale)]]} />
            <p className="mt-3 text-xs text-ink-3">{L("Revenu, âge et profession ne sont pas utilisés par le modèle.", "Income, age and occupation are not used by the model.")}</p>
          </Panel>}
          <Panel title={L("Grille des grades", "Grade scale")}><KeyValue rows={Object.entries(a.grade_bounds).map(([g, r]) => [g, r])} /></Panel>
        </div>
      </div>
    </div>
  );
}
