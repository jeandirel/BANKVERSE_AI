"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Download } from "lucide-react";
import { useMemo, useState } from "react";
import { BarsChart } from "@/components/ui/charts";
import { DataTable } from "@/components/ui/data-table";
import { ErrorState, Ledger, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Badge, Button, Input, Select } from "@/components/ui/primitives";
import { DemoTag, RiskBadge, ScoreCell, type Band } from "@/components/ui/risk";
import { download, get, qs } from "@/lib/api";
import { fmtCompact, fmtMoney, fmtNumber, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe } from "@/lib/session";
import { TYPOLOGY } from "@/lib/domain/aml";

type Row = {
  id: string; score: number; risk_band: Band; status: string; typologies: string[]; rules: string[]; ml_percentile: number | null;
  created_at: string; case_id: string | null; account: { id: string; iban: string; type: string };
  customer: { id: string; name: string; ref: string; type: string; is_demo: boolean };
};
type Overview = {
  alerts_by_status: Record<string, number>; open_by_band: Record<string, number>; open_by_typology: Record<string, number>;
  accounts_scored: number; transfers: { count: number; volume: number };
  model: null | { status: string; version: number | null; test_pr_auc: number | null; policy: string }; disclaimer: string;
};

export default function AmlWorkspace() {
  const { t, locale } = useI18n();
  const L = useL();
  const { data: me } = useMe();
  const [status, setStatus] = useState("open");
  const [band, setBand] = useState("");
  const [typology, setTypology] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const ov = useQuery({ queryKey: ["aml", "overview"], queryFn: () => get<Overview>("/aml/overview") });
  const alerts = useQuery({
    queryKey: ["aml", "alerts", status, band, typology, q, page],
    queryFn: () => get<{ total: number; items: Row[] }>(`/aml/alerts${qs({ status, band, typology, q, page, page_size: 25 })}`),
    placeholderData: (p) => p,
  });
  const cols = useMemo<ColumnDef<Row, unknown>[]>(() => [
    { id: "score", header: t("common.score"), cell: ({ row }) => <ScoreCell score={row.original.score} band={row.original.risk_band} /> },
    { id: "band", header: L("Niveau", "Level"), cell: ({ row }) => <RiskBadge band={row.original.risk_band} /> },
    { id: "cust", header: t("common.customer"), cell: ({ row }) => <div><p>{row.original.customer.name}</p><p className="text-xs text-ink-3 num">{row.original.account.iban}</p></div> },
    { id: "typ", header: L("Typologies", "Typologies"), cell: ({ row }) => <div className="flex flex-wrap gap-1">{row.original.typologies.map((x) => <Badge key={x}>{TYPOLOGY[x]?.[locale] ?? x}</Badge>)}</div> },
    { id: "ml", header: L("Rang du modèle", "Model rank"), meta: { align: "right" }, cell: ({ row }) => row.original.ml_percentile == null ? "—" : fmtPct(row.original.ml_percentile, locale, 1) },
    { id: "status", header: t("common.status"), cell: ({ row }) => <Badge tone={row.original.status === "new" ? "info" : "neutral"}>{row.original.status}</Badge> },
  ], [t, L, locale]);
  const o = ov.data;
  const open = o ? Object.entries(o.alerts_by_status).filter(([k]) => !k.startsWith("closed")).reduce((a, [, v]) => a + v, 0) : 0;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={L("Espace d'investigation LCB-FT", "AML investigation workspace")}
        subtitle={L("Typologies détectées par règles interprétables et par un modèle de graphe ; chaque alerte est justifiée par des opérations.", "Typologies detected by interpretable rules and a graph model; every alert is backed by transactions.")}
        meta={me?.org.kind === "demo" ? <DemoTag /> : undefined}
        actions={<Button icon={<Download className="size-4" />} onClick={() => download("/api/v1/exports/aml_alerts.csv", "aml_alerts.csv")}>{t("common.export")} CSV</Button>} />
      <Notice tone="warn">{L("Une alerte LCB-FT n'est pas une preuve de blanchiment. Le système aide l'investigation ; la décision et toute déclaration de soupçon relèvent des personnes habilitées.", "An AML alert is not proof of money laundering. The system supports investigation; decisions and any suspicious activity report belong to authorised staff.")}</Notice>
      {ov.error ? <ErrorState error={ov.error} /> : !o ? <LoadingBlock /> : (
        <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
          <Ledger items={[
            { label: L("Alertes ouvertes", "Open alerts"), value: fmtNumber(open, locale) },
            { label: t("risk.critical"), value: fmtNumber(o.open_by_band.critical ?? 0, locale), tone: o.open_by_band.critical ? "critical" : undefined },
            { label: L("Comptes analysés", "Accounts scored"), value: fmtNumber(o.accounts_scored, locale) },
            { label: L("Virements analysés", "Transfers analysed"), value: fmtCompact(o.transfers.count, locale), hint: fmtMoney(o.transfers.volume, locale, "EUR", 0) },
            { label: L("Modèle", "Model"), value: o.model?.version ? `v${o.model.version}` : "—", hint: o.model?.test_pr_auc ? `PR-AUC ${o.model.test_pr_auc.toFixed(3)} · ${o.model.policy}` : L("non déployé", "not deployed") },
          ]} />
          <Panel title={L("Alertes ouvertes par typologie", "Open alerts by typology")}>
            <BarsChart horizontal height={200} data={Object.entries(o.open_by_typology).map(([k, v]) => ({ t: TYPOLOGY[k]?.[locale] ?? k, n: v }))}
              xKey="t" series={[{ key: "n", label: L("Alertes", "Alerts") }]} />
          </Panel>
        </div>
      )}
      <Panel title={L("Alertes", "Alerts")} flush actions={
        <div className="flex flex-wrap gap-2">
          <Input className="h-8 w-48" placeholder={L("Client, IBAN…", "Customer, IBAN…")} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label={t("common.search")} />
          <Select className="h-8 w-36" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label={t("common.status")}>
            <option value="open">{L("Ouvertes", "Open")}</option><option value="closed_no_action,closed_reported">{L("Clôturées", "Closed")}</option><option value="">{t("common.all")}</option>
          </Select>
          <Select className="h-8 w-32" value={band} onChange={(e) => { setBand(e.target.value); setPage(1); }} aria-label={L("Niveau", "Level")}>
            <option value="">{L("Tous niveaux", "All levels")}</option><option value="critical">{t("risk.critical")}</option><option value="high">{t("risk.high")}</option><option value="medium">{t("risk.medium")}</option>
          </Select>
          <Select className="h-8 w-44" value={typology} onChange={(e) => { setTypology(e.target.value); setPage(1); }} aria-label={L("Typologie", "Typology")}>
            <option value="">{L("Toutes typologies", "All typologies")}</option>
            {Object.entries(TYPOLOGY).map(([k, v]) => <option key={k} value={k}>{v[locale]}</option>)}
          </Select>
        </div>}>
        {alerts.error ? <ErrorState error={alerts.error} /> : (
          <DataTable columns={cols} data={alerts.data?.items} loading={alerts.isLoading} total={alerts.data?.total} page={page} onPageChange={setPage}
            rowHref={(r) => `/app/aml/alerts/${r.id}`} />
        )}
      </Panel>
    </div>
  );
}
