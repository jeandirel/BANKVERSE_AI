"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, FlaskConical } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { BarsChart, TimeSeriesChart } from "@/components/ui/charts";
import { DataTable } from "@/components/ui/data-table";
import { ErrorState, KeyValue, Ledger, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Badge, Button, Input, Select } from "@/components/ui/primitives";
import { DemoTag, RiskBadge, ScoreCell, type Band } from "@/components/ui/risk";
import { download, get, qs } from "@/lib/api";
import { fmtCompact, fmtDate, fmtMoney, fmtNumber, fmtPct, fmtRelative } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import type { MessageKey } from "@/lib/messages";
import { statusLabel, type FraudAlertRow } from "@/lib/domain/fraud";
import { useCan, useMe } from "@/lib/session";

type Overview = {
  alerts_by_status: Record<string, number>;
  open_alerts_by_band: Record<string, number>;
  transactions: { count: number; scored: number; volume: number };
  daily: { day: string; transactions: number; alerts: number }[];
  overdue_alerts: number;
  open_exposure: number;
  analyst_feedback: { band: string; confirmed_fraud: number; false_positive: number }[];
  top_alert_mcc: { mcc: number; alerts: number }[];
  model: null | { status: string; version?: number; algorithm?: string; test_pr_auc?: number; test_roc_auc?: number; threshold?: number; deployed_at?: string };
  demo_simulation: null | { simulated_fraud_transactions: number; simulated_fraud_alerted: number; alerts_total: number; note: string };
};

const FILTERS = [
  { key: "open", status: "open", assigned: undefined, label: "fraud.filter.open" },
  { key: "mine", status: "open", assigned: "me", label: "fraud.filter.mine" },
  { key: "unassigned", status: "new", assigned: "unassigned", label: "fraud.filter.unassigned" },
  { key: "closed", status: "closed_fraud,closed_legitimate", assigned: undefined, label: "fraud.filter.closed" },
] as const;

export default function FraudCenter() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const { data: me } = useMe();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("open");
  const [band, setBand] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("priority");
  const [page, setPage] = useState(1);
  const f = FILTERS.find((x) => x.key === filter)!;

  const overview = useQuery({ queryKey: ["fraud", "overview"], queryFn: () => get<Overview>("/fraud/overview?days=120") });
  const alerts = useQuery({
    queryKey: ["fraud", "alerts", filter, band, q, sort, page],
    queryFn: () => get<{ total: number; items: FraudAlertRow[] }>(`/fraud/alerts${qs({ status: f.status, assigned: f.assigned, band, q, sort, page, page_size: 25 })}`),
    placeholderData: (prev) => prev,
  });

  const columns = useMemo<ColumnDef<FraudAlertRow, unknown>[]>(() => [
    { id: "score", header: t("common.score"), cell: ({ row }) => <ScoreCell score={row.original.score} band={row.original.risk_band} /> },
    { id: "band", header: L("Niveau", "Level"), cell: ({ row }) => <RiskBadge band={row.original.risk_band} /> },
    {
      id: "customer", header: t("common.customer"),
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="truncate text-ink">{row.original.customer.name}</p>
          <p className="text-xs text-ink-3">{row.original.customer.ref}</p>
        </div>
      ),
    },
    {
      id: "merchant", header: t("fraud.col.merchant"),
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[240px]">
          <p className="truncate">{row.original.transaction.merchant}</p>
          <p className="text-xs text-ink-3 truncate">MCC {row.original.transaction.mcc} · {row.original.transaction.merchant_city}</p>
        </div>
      ),
    },
    { id: "amount", header: t("common.amount"), meta: { align: "right" }, cell: ({ row }) => fmtMoney(row.original.transaction.amount, locale) },
    { id: "reason", header: t("fraud.col.reason"), cell: ({ row }) => <span className="text-ink-2 text-[13px]">{row.original.top_reason ?? "—"}</span> },
    { id: "status", header: t("common.status"), cell: ({ row }) => <Badge tone={row.original.status.startsWith("closed") ? "neutral" : row.original.status === "escalated" ? "high" : "info"}>{statusLabel(t, row.original.status)}</Badge> },
    {
      id: "sla", header: t("fraud.col.sla"),
      cell: ({ row }) => {
        const due = row.original.sla_due_at;
        if (!due || row.original.status.startsWith("closed")) return <span className="text-ink-3">—</span>;
        const late = new Date(due).getTime() < Date.now();
        return <span className={late ? "text-risk-high font-medium" : "text-ink-2"}>{fmtRelative(due, locale)}</span>;
      },
    },
    { id: "ts", header: t("common.date"), cell: ({ row }) => <span className="text-ink-2 text-[13px] whitespace-nowrap">{fmtDate(row.original.transaction.ts, locale, true)}</span> },
  ], [t, L, locale]);

  const ov = overview.data;
  const open = ov ? (ov.alerts_by_status.new ?? 0) + (ov.alerts_by_status.in_review ?? 0) + (ov.alerts_by_status.escalated ?? 0) : null;
  const daily = (ov?.daily ?? []).map((d) => ({ ...d, label: d.day.slice(5) }));

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("fraud.title")}
        subtitle={t("fraud.subtitle")}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined}
        actions={
          <>
            {can("fraud.simulate") && <Link href="/app/fraud/simulator"><Button icon={<FlaskConical className="size-4" />}>{t("fraud.sim.title")}</Button></Link>}
            {can("reports.read") && <Button icon={<Download className="size-4" />} onClick={() => download("/api/v1/exports/fraud_alerts.csv", "fraud_alerts.csv")}>{t("common.export")} CSV</Button>}
          </>
        }
      />
      {overview.error ? <ErrorState error={overview.error} onRetry={() => overview.refetch()} /> : !ov ? <LoadingBlock /> : (
        <>
          {ov.model === null || ov.model.status === "not_deployed" ? <Notice tone="warn">{t("fraud.model.none")}</Notice> : null}
          <Ledger items={[
            { label: t("fraud.kpi.open"), value: fmtNumber(open, locale) },
            { label: t("fraud.kpi.critical"), value: fmtNumber(ov.open_alerts_by_band.critical, locale), tone: ov.open_alerts_by_band.critical ? "critical" : undefined },
            { label: t("fraud.kpi.overdue"), value: fmtNumber(ov.overdue_alerts, locale), tone: ov.overdue_alerts ? "high" : undefined },
            { label: t("fraud.kpi.exposure"), value: fmtMoney(ov.open_exposure, locale, "EUR", 0), hint: L("Σ score × montant des alertes ouvertes", "Σ score × amount of open alerts") },
            { label: t("fraud.kpi.scored"), value: fmtCompact(ov.transactions.scored, locale), hint: L(`sur ${fmtCompact(ov.transactions.count, locale)} transactions`, `of ${fmtCompact(ov.transactions.count, locale)} transactions`) },
          ]} />
        </>
      )}

      <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
        <Panel title={t("fraud.queue")} flush
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Input placeholder={t("common.search")} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="h-8 w-44" aria-label={t("common.search")} />
              <Select value={band} onChange={(e) => { setBand(e.target.value); setPage(1); }} className="h-8 w-32" aria-label={L("Niveau", "Level")}>
                <option value="">{L("Tous niveaux", "All levels")}</option>
                <option value="critical">{t("risk.critical")}</option>
                <option value="high">{t("risk.high")}</option>
                <option value="medium">{t("risk.medium")}</option>
              </Select>
              <Select value={sort} onChange={(e) => setSort(e.target.value)} className="h-8 w-36" aria-label={L("Tri", "Sort")}>
                <option value="priority">{L("Priorité", "Priority")}</option>
                <option value="score">{t("common.score")}</option>
                <option value="recent">{L("Plus récentes", "Most recent")}</option>
                <option value="amount">{t("common.amount")}</option>
                <option value="sla">{t("fraud.col.sla")}</option>
              </Select>
            </div>
          }>
          <div className="flex gap-1 px-3 pt-2 border-b border-line" role="tablist">
            {FILTERS.map((x) => (
              <button key={x.key} role="tab" aria-selected={filter === x.key} onClick={() => { setFilter(x.key); setPage(1); }}
                className={`px-2.5 py-2 text-[13px] border-b-2 -mb-px ${filter === x.key ? "border-accent text-ink font-medium" : "border-transparent text-ink-3 hover:text-ink"}`}>
                {t(x.label)}
              </button>
            ))}
          </div>
          {alerts.error ? <ErrorState error={alerts.error} onRetry={() => alerts.refetch()} /> : (
            <DataTable columns={columns} data={alerts.data?.items} loading={alerts.isLoading} total={alerts.data?.total}
              page={page} pageSize={25} onPageChange={setPage} rowHref={(r) => `/app/fraud/alerts/${r.id}`} caption={t("fraud.queue")} />
          )}
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel title={t("fraud.model.card")}>
            {ov?.model && ov.model.status === "production" ? (
              <KeyValue rows={[
                [t("common.version"), <span key="v" className="num">v{ov.model.version}</span>],
                [L("Algorithme", "Algorithm"), ov.model.algorithm],
                ["PR-AUC (test 2019)", <span key="p" className="num">{ov.model.test_pr_auc?.toFixed(3)}</span>],
                ["ROC-AUC (test 2019)", <span key="r" className="num">{ov.model.test_roc_auc?.toFixed(3)}</span>],
                [L("Seuil d'alerte", "Alert threshold"), <span key="s" className="num">{ov.model.threshold?.toFixed(3)}</span>],
                [L("Déployé", "Deployed"), fmtDate(ov.model.deployed_at, locale, true)],
              ]} />
            ) : <p className="text-sm text-ink-3">{t("fraud.model.none")}</p>}
            {can("models.read") && <Link href="/app/models/fraud_card_transactions" className="mt-3 inline-block text-[13px] text-accent hover:underline">{L("Fiche modèle et comparaison", "Model card and comparison")}</Link>}
          </Panel>
          {ov?.demo_simulation && (
            <Panel title={t("fraud.demoSim")} description={ov.demo_simulation.note}>
              <KeyValue rows={[
                [L("Transactions frauduleuses simulées", "Simulated fraudulent transactions"), <span key="a" className="num">{ov.demo_simulation.simulated_fraud_transactions}</span>],
                [L("Dont alertées par le modèle", "Of which alerted by the model"), <span key="b" className="num">{ov.demo_simulation.simulated_fraud_alerted} ({fmtPct(ov.demo_simulation.simulated_fraud_transactions ? ov.demo_simulation.simulated_fraud_alerted / ov.demo_simulation.simulated_fraud_transactions : 0, locale)})</span>],
                [L("Alertes au total", "Total alerts"), <span key="c" className="num">{ov.demo_simulation.alerts_total}</span>],
              ]} />
              <p className="mt-3 text-xs text-ink-3 leading-relaxed">{L("Indicateur de démonstration uniquement : la performance du modèle est mesurée sur le jeu de test public (IBM TabFormer 2019).", "Demonstration indicator only: model performance is measured on the public test set (IBM TabFormer 2019).")}</p>
            </Panel>
          )}
        </div>
      </div>

      {ov && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel title={t("fraud.chart.alerts")}>
            <BarsChart data={daily} xKey="label" series={[{ key: "alerts", label: L("Alertes", "Alerts"), color: "var(--series-1)" }]} height={220} />
          </Panel>
          <Panel title={t("fraud.chart.transactions")}>
            <TimeSeriesChart data={daily} xKey="label" series={[{ key: "transactions", label: L("Transactions", "Transactions"), color: "var(--series-2)" }]} height={220} />
          </Panel>
          <Panel title={L("Catégories marchandes les plus alertées", "Most alerted merchant categories")}>
            <BarsChart horizontal data={ov.top_alert_mcc.map((m) => ({ mcc: `MCC ${m.mcc}`, alerts: m.alerts }))} xKey="mcc"
              series={[{ key: "alerts", label: L("Alertes", "Alerts"), color: "var(--series-1)" }]} height={260} />
          </Panel>
          <Panel title={L("Retour des analystes", "Analyst feedback")} description={L("Alertes clôturées, par niveau de risque", "Closed alerts by risk level")}>
            {ov.analyst_feedback.length === 0 ? <p className="text-sm text-ink-3">{L("Aucune alerte clôturée pour l'instant.", "No closed alerts yet.")}</p> : (
              <BarsChart data={ov.analyst_feedback.map((f) => ({ band: t(`risk.${f.band}` as MessageKey), fraude: f.confirmed_fraud, legit: f.false_positive }))}
                xKey="band" series={[{ key: "fraude", label: L("Fraude confirmée", "Confirmed fraud") }, { key: "legit", label: L("Faux positif", "False positive") }]} height={240} />
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
