"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, FileSpreadsheet } from "lucide-react";
import { ErrorState, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { useToast } from "@/components/ui/overlay";
import { Badge, Button } from "@/components/ui/primitives";
import { ApiError, download, get } from "@/lib/api";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type Report = { id: string; kind: string; title: string; format: string; created_at: string; size: number; sha256: string; source_type: string | null };
const KIND: Record<string, [string, string]> = { case: ["Dossier", "Case"], governance: ["Gouvernance", "Governance"], aml: ["LCB-FT", "AML"], fraud: ["Fraude", "Fraud"] };

export default function ReportsPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const toast = useToast();
  const q = useQuery({ queryKey: ["reports"], queryFn: () => get<Report[]>("/reports") });
  const dl = (path: string, name: string) => download(path, name).catch((e) => toast(e instanceof ApiError ? e.message : String(e), "error"));
  const exports = [
    { key: "fraud_alerts", label: L("Alertes de fraude", "Fraud alerts"), perm: "fraud.read" },
    { key: "aml_alerts", label: L("Alertes LCB-FT", "AML alerts"), perm: "aml.read" },
    { key: "cases", label: L("Dossiers", "Cases"), perm: "cases.read" },
  ].filter((e) => can(e.perm));
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.reports")} subtitle={L("Rapports PDF horodatés et exports CSV ; chaque téléchargement est tracé.", "Timestamped PDF reports and CSV exports; every download is logged.")} />
      <Notice>{L("Les rapports produits dans la banque DEMO portent la mention DEMO. L'empreinte SHA-256 permet de vérifier qu'un fichier n'a pas été modifié.", "Reports produced in the DEMO bank carry the DEMO mark. The SHA-256 fingerprint lets you check a file has not been altered.")}</Notice>
      {exports.length > 0 && <Panel title={L("Exports CSV", "CSV exports")}>
        <div className="flex flex-wrap gap-2">{exports.map((e) => <Button key={e.key} icon={<FileSpreadsheet className="size-4" />} onClick={() => dl(`/exports/${e.key}.csv`, `${e.key}.csv`)}>{e.label}</Button>)}</div>
      </Panel>}
      <Panel title={L("Rapports", "Reports")} flush>
        {q.error ? <ErrorState error={q.error} /> : !q.data ? <LoadingBlock /> : q.data.length === 0 ? <p className="px-4 py-6 text-sm text-ink-3">{L("Aucun rapport. Générez-en depuis un dossier ou la page Gouvernance.", "No report. Generate one from a case or the Governance page.")}</p> : (
          <ul>{q.data.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5 last:border-0">
              <Badge>{locale === "en" ? KIND[r.kind]?.[1] ?? r.kind : KIND[r.kind]?.[0] ?? r.kind}</Badge>
              <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{r.title}</p><p className="num text-xs text-ink-3">SHA-256 {r.sha256.slice(0, 20)}… · {fmtNumber(r.size / 1024, locale, 0)} Ko</p></div>
              <span className="text-xs text-ink-3">{fmtDate(r.created_at, locale, true)}</span>
              <Button size="sm" icon={<Download className="size-3.5" />} onClick={() => dl(`/reports/${r.id}/download`, `${r.title}.pdf`)}>{L("Télécharger", "Download")}</Button>
            </li>))}</ul>
        )}
      </Panel>
    </div>
  );
}
