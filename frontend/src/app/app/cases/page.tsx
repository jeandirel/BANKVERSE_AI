"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Download } from "lucide-react";
import { useMemo, useState } from "react";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState, ErrorState, PageHeader, Panel } from "@/components/ui/layout";
import { Badge, Button, Input, Select } from "@/components/ui/primitives";
import { download, get, qs } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

type Row = { id: string; reference: string; type: string; title: string; status: string; priority: string; created_at: string; decision: string | null; customer: string | null };

export default function CasesPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const [status, setStatus] = useState("open,investigating,pending_decision");
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const cases = useQuery({
    queryKey: ["cases", status, type, q, page],
    queryFn: () => get<{ total: number; items: Row[] }>(`/cases${qs({ status, case_type: type, q, page, page_size: 25 })}`),
    placeholderData: (p) => p,
  });
  const cols = useMemo<ColumnDef<Row, unknown>[]>(() => [
    { accessorKey: "reference", header: L("Référence", "Reference"), cell: ({ getValue }) => <span className="num font-medium">{String(getValue())}</span> },
    { accessorKey: "type", header: L("Type", "Type"), cell: ({ getValue }) => <Badge tone="accent">{String(getValue()).toUpperCase()}</Badge> },
    { accessorKey: "title", header: L("Intitulé", "Title"), cell: ({ getValue }) => <span className="block max-w-md truncate">{String(getValue())}</span> },
    { accessorKey: "customer", header: t("common.customer") },
    { accessorKey: "priority", header: L("Priorité", "Priority"), cell: ({ getValue }) => { const v = String(getValue()); return <Badge tone={v === "critical" ? "critical" : v === "high" ? "high" : "neutral"}>{v}</Badge>; } },
    { accessorKey: "status", header: t("common.status"), cell: ({ row }) => <span className="text-ink-2">{row.original.status}{row.original.decision ? ` · ${row.original.decision}` : ""}</span> },
    { accessorKey: "created_at", header: t("common.created"), cell: ({ getValue }) => fmtDate(String(getValue()), locale, true) },
  ], [t, L, locale]);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.cases")} subtitle={L("Dossiers d'investigation ouverts depuis les alertes fraude, LCB-FT, crédit et KYC.", "Investigation cases opened from fraud, AML, credit and KYC alerts.")}
        actions={<Button icon={<Download className="size-4" />} onClick={() => download("/api/v1/exports/cases.csv", "cases.csv")}>{t("common.export")} CSV</Button>} />
      <Panel flush actions={
        <div className="flex flex-wrap gap-2">
          <Input className="h-8 w-48" placeholder={t("common.search")} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label={t("common.search")} />
          <Select className="h-8 w-44" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label={t("common.status")}>
            <option value="open,investigating,pending_decision">{L("En cours", "Active")}</option><option value="closed">{L("Clôturés", "Closed")}</option><option value="">{t("common.all")}</option>
          </Select>
          <Select className="h-8 w-32" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }} aria-label={L("Type", "Type")}>
            <option value="">{L("Tous types", "All types")}</option><option value="fraud">Fraude</option><option value="aml">LCB-FT</option><option value="credit">Crédit</option><option value="kyc">KYC</option>
          </Select>
        </div>} title={L("Dossiers", "Cases")}>
        {cases.error ? <ErrorState error={cases.error} /> : (
          <DataTable columns={cols} data={cases.data?.items} loading={cases.isLoading} total={cases.data?.total} page={page} onPageChange={setPage}
            rowHref={(r) => `/app/cases/${r.id}`} empty={<EmptyState title={L("Aucun dossier", "No cases")} body={L("Ouvrez un dossier depuis une alerte fraude ou LCB-FT pour démarrer une investigation.", "Open a case from a fraud or AML alert to start an investigation.")} />} />
        )}
      </Panel>
    </div>
  );
}
