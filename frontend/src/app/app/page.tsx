"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { BarsChart, TimeSeriesChart } from "@/components/ui/charts";
import { ErrorState, Ledger, LoadingBlock, PageHeader, Panel } from "@/components/ui/layout";
import { DemoTag } from "@/components/ui/risk";
import { get } from "@/lib/api";
import { fmtDate, fmtMoney, fmtNumber, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe } from "@/lib/session";

type Bands = { critical: number; high: number; medium: number };
type Dash = {
  org: { name: string; kind: string }; generated_at: string;
  fraud?: { open_by_band: Bands; overdue: number; open_exposure: number; transactions_30d: number; daily_alerts: { day: string; alerts: number }[] };
  aml?: { open_by_band: Bands; top_typologies: { typology: string; alerts: number }[] };
  credit?: { by_status: Record<string, number>; by_grade: Record<string, number>; average_pd: number | null };
  documents?: { needs_review: number }; cases?: { by_status: Record<string, number>; open: number };
  customers?: { complaints_30d: number; top_topics: { topic: string; messages: number }[] }; agents?: { pending_approvals: number };
  models?: { in_production: { name: string; version: number; headline: { label: string; value: number | string } | null }[]; pending_review: number; total: number };
  incidents_open?: number; usage: { metric: string; used: number; limit: number | null }[]; attention: { kind: string; count: number; href: string }[];
};

const ATTENTION: Record<string, [string, string]> = {
  fraud_overdue: ["alertes fraude hors délai", "overdue fraud alerts"],
  documents_review: ["documents à revoir", "documents to review"],
  agent_approvals: ["actions d'agents à approuver", "agent actions to approve"],
  incidents: ["incidents de gouvernance ouverts", "open governance incidents"],
};
const METRIC: Record<string, [string, string]> = {
  api_calls: ["Appels API", "API calls"], fraud_scoring: ["Scorings fraude", "Fraud scorings"], copilot_queries: ["Questions copilote", "Copilot questions"],
  document_pages: ["Pages de documents", "Document pages"], agent_runs: ["Exécutions d'agents", "Agent runs"], forecast_runs: ["Prévisions", "Forecasts"],
  credit_scoring: ["Scorings crédit", "Credit scorings"],
};

export default function DashboardPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const { data: me } = useMe();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => get<Dash>("/dashboard"), refetchInterval: 60_000 });
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  if (!d) return <LoadingBlock />;
  const pick2 = (p: [string, string]) => (locale === "en" ? p[1] : p[0]);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.dashboard")} subtitle={L(`${d.org.name} — situation au ${fmtDate(d.generated_at, locale, true)}`, `${d.org.name} — status at ${fmtDate(d.generated_at, locale, true)}`)}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined} />
      {d.attention.length > 0 && (
        <section aria-label={L("À traiter", "Needs attention")} className="rounded-[var(--radius-panel)] border border-risk-high/30 bg-risk-high-soft">
          <ul className="flex flex-wrap divide-x divide-risk-high/20">
            {d.attention.map((a) => (
              <li key={a.kind}><Link href={a.href} className="flex items-baseline gap-2 px-4 py-3 hover:underline underline-offset-2">
                <span className="num text-xl font-semibold text-risk-high">{a.count}</span><span className="text-sm">{pick2(ATTENTION[a.kind] ?? [a.kind, a.kind])}</span>
                <ArrowUpRight className="size-3.5 text-ink-3" aria-hidden /></Link></li>
            ))}
          </ul>
        </section>
      )}
      {d.fraud && (
        <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <Panel title={L("Fraude carte", "Card fraud")} actions={<Link className="text-sm text-accent hover:underline" href="/app/fraud">{L("Ouvrir", "Open")}</Link>}>
            <Ledger items={[
              { label: L("Critiques ouvertes", "Open critical"), value: fmtNumber(d.fraud.open_by_band.critical, locale), tone: "critical" },
              { label: L("Élevées ouvertes", "Open high"), value: fmtNumber(d.fraud.open_by_band.high, locale), tone: "high" },
              { label: L("Hors délai", "Overdue"), value: fmtNumber(d.fraud.overdue, locale), tone: d.fraud.overdue ? "high" : undefined },
              { label: L("Exposition ouverte", "Open exposure"), value: fmtMoney(d.fraud.open_exposure, locale, "EUR", 0) },
            ]} />
            <div className="mt-4"><TimeSeriesChart data={d.fraud.daily_alerts} xKey="day" series={[{ key: "alerts", label: L("Alertes créées", "Alerts created") }]} height={180}
              title={L(`Alertes par jour (30 j, ${fmtNumber(d.fraud.transactions_30d, locale)} transactions)`, `Alerts per day (30 d, ${fmtNumber(d.fraud.transactions_30d, locale)} transactions)`)} /></div>
          </Panel>
          {d.aml && <Panel title={L("LCB-FT", "AML")} actions={<Link className="text-sm text-accent hover:underline" href="/app/aml">{L("Ouvrir", "Open")}</Link>}>
            <Ledger items={[
              { label: L("Critiques", "Critical"), value: fmtNumber(d.aml.open_by_band.critical, locale), tone: "critical" },
              { label: L("Élevées", "High"), value: fmtNumber(d.aml.open_by_band.high, locale), tone: "high" },
              { label: L("Moyennes", "Medium"), value: fmtNumber(d.aml.open_by_band.medium, locale), tone: "medium" },
            ]} />
            <div className="mt-4">{d.aml.top_typologies.length ? <BarsChart horizontal data={d.aml.top_typologies} xKey="typology" series={[{ key: "alerts", label: L("Alertes ouvertes", "Open alerts") }]}
              height={Math.max(150, d.aml.top_typologies.length * 32)} title={L("Typologies des alertes ouvertes", "Typologies of open alerts")} /> : <p className="text-sm text-ink-3">{L("Aucune alerte ouverte.", "No open alert.")}</p>}</div>
          </Panel>}
        </div>
      )}
      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
        {d.credit && <Panel title={L("Crédit", "Credit")} actions={<Link className="text-sm text-accent hover:underline" href="/app/credit">{L("Ouvrir", "Open")}</Link>}>
          <p className="text-sm text-ink-2 mb-2">{L("PD moyenne", "Average PD")} <span className="num font-semibold text-ink">{d.credit.average_pd == null ? "—" : fmtPct(d.credit.average_pd, locale)}</span> · {L("à décider", "to decide")} <span className="num font-semibold text-ink">{(d.credit.by_status.scored ?? 0) + (d.credit.by_status.under_review ?? 0)}</span></p>
          <BarsChart data={Object.entries(d.credit.by_grade).map(([g, n]) => ({ g, n }))} xKey="g" series={[{ key: "n", label: L("Demandes", "Applications") }]} height={160} />
        </Panel>}
        {d.customers && <Panel title={L("Relation client", "Customer relationship")} actions={<Link className="text-sm text-accent hover:underline" href="/app/customers">{L("Ouvrir", "Open")}</Link>}>
          <p className="text-sm text-ink-2 mb-2">{L("Réclamations (30 j)", "Complaints (30 d)")} <span className="num font-semibold text-ink">{d.customers.complaints_30d}</span></p>
          {d.customers.top_topics.length ? <BarsChart horizontal data={d.customers.top_topics} xKey="topic" series={[{ key: "messages", label: L("Messages", "Messages") }]} height={Math.max(150, d.customers.top_topics.length * 30)} /> : <p className="text-sm text-ink-3">—</p>}
        </Panel>}
        <Panel title={L("Activité", "Activity")}>
          <ul className="flex flex-col divide-y divide-line text-sm">
            {d.cases && <li className="flex justify-between py-2"><Link href="/app/cases" className="hover:underline">{L("Dossiers ouverts", "Open cases")}</Link><span className="num font-semibold">{d.cases.open}</span></li>}
            {d.documents && <li className="flex justify-between py-2"><Link href="/app/documents" className="hover:underline">{L("Documents à revoir", "Documents to review")}</Link><span className="num font-semibold">{d.documents.needs_review}</span></li>}
            {d.agents && <li className="flex justify-between py-2"><Link href="/app/agents" className="hover:underline">{L("Actions d'agents en attente", "Pending agent actions")}</Link><span className="num font-semibold">{d.agents.pending_approvals}</span></li>}
            {d.incidents_open !== undefined && <li className="flex justify-between py-2"><Link href="/app/governance" className="hover:underline">{L("Incidents ouverts", "Open incidents")}</Link><span className="num font-semibold">{d.incidents_open}</span></li>}
          </ul>
        </Panel>
        {d.models && <Panel title={L("Modèles en production", "Models in production")} actions={<Link className="text-sm text-accent hover:underline" href="/app/models">{L("Registre", "Registry")}</Link>}
          description={L(`${d.models.in_production.length} / ${d.models.total} · ${d.models.pending_review} version(s) en revue`, `${d.models.in_production.length} / ${d.models.total} · ${d.models.pending_review} version(s) in review`)}>
          {d.models.in_production.length === 0 ? <p className="text-sm text-ink-3">{L("Aucun modèle déployé.", "No model deployed.")}</p> :
            <ul className="flex flex-col divide-y divide-line text-sm">{d.models.in_production.map((m) => (
              <li key={m.name} className="flex items-baseline justify-between gap-3 py-1.5"><span>{m.name} <span className="text-ink-3">v{m.version}</span></span>
                {m.headline && <span className="text-xs text-ink-3">{m.headline.label} <span className="num text-ink">{typeof m.headline.value === "number" ? m.headline.value.toFixed(3) : m.headline.value}</span></span>}</li>))}</ul>}
        </Panel>}
        <Panel title={L("Consommation du mois", "Usage this month")}>
          <ul className="flex flex-col gap-2.5">{d.usage.filter((u) => u.limit).map((u) => {
            const r = Math.min(1, u.used / (u.limit as number));
            return (
              <li key={u.metric}>
                <div className="flex justify-between text-sm"><span>{pick2(METRIC[u.metric] ?? [u.metric, u.metric])}</span><span className="num text-ink-3">{fmtNumber(u.used, locale)} / {fmtNumber(u.limit, locale)}</span></div>
                <div className="mt-1 h-1.5 rounded-full bg-surface-3" role="progressbar" aria-valuenow={Math.round(r * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={pick2(METRIC[u.metric] ?? [u.metric, u.metric])}>
                  <div className={`h-full rounded-full ${r > 0.9 ? "bg-risk-high" : "bg-accent"}`} style={{ width: `${Math.max(r * 100, 1)}%` }} /></div>
              </li>);
          })}</ul>
        </Panel>
      </div>
    </div>
  );
}
