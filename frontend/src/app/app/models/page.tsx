"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ErrorState, Ledger, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Badge } from "@/components/ui/primitives";
import { get } from "@/lib/api";
import { driftTone, MODULE_LABELS } from "@/lib/domain/models";
import { fmtDate, fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

type Headline = { label: string; value: number | string } | null;
type ModelRow = { name: string; module: string; task: string; risk_tier: string; description: string; versions: number; pending_review: number;
  production: { version: number; algorithm: string; deployed_at: string; trained_by: string; headline: Headline } | null;
  latest: { version: number; status: string; created_at: string; headline: Headline } | null;
  live: { inferences: number; error_rate: number | null; mean_latency_ms: number | null } | null;
  drift: { status: string | null; max_psi: number | null; samples: number | null } | null };

export default function ModelsPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const q = useQuery({ queryKey: ["models"], queryFn: () => get<{ models: ModelRow[]; notice: string; can_manage_lifecycle: boolean }>("/models") });
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  const fmtH = (h: Headline) => (h ? `${h.label} ${typeof h.value === "number" ? h.value.toFixed(3) : h.value}` : "—");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.models")} subtitle={L("Registre des modèles : versions, preuves d'évaluation, revue à quatre yeux, déploiement, retour arrière et dérive.", "Model registry: versions, evaluation evidence, four-eyes review, deployment, rollback and drift.")} />
      {!d ? <LoadingBlock /> : (
        <>
          <Notice>{d.notice} {d.can_manage_lifecycle ? "" : L("Les modèles sont partagés par toutes les banques : leur cycle de vie est géré par l'équipe MLOps de la plateforme.", "Models are shared by all banks: their lifecycle is managed by the platform MLOps team.")}</Notice>
          <Ledger items={[
            { label: L("Modèles", "Models"), value: fmtNumber(d.models.length, locale) },
            { label: L("En production", "In production"), value: fmtNumber(d.models.filter((m) => m.production).length, locale), tone: "ok" },
            { label: L("Versions en revue", "Versions in review"), value: fmtNumber(d.models.reduce((a, m) => a + m.pending_review, 0), locale), tone: "medium" },
            { label: L("Dérives significatives", "Significant drifts"), value: fmtNumber(d.models.filter((m) => m.drift?.status === "significant").length, locale), tone: "high" },
          ]} />
          <Panel flush>
            <div className="overflow-x-auto"><table className="w-full text-sm">
              <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">{[L("Modèle", "Model"), L("Module", "Module"), L("Production", "Production"), L("Indicateur (test)", "Metric (test)"), L("Inférences 30 j", "Inferences 30 d"), L("Dérive", "Drift"), L("Dernière version", "Latest version")].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>{d.models.map((m) => (
                <tr key={m.name} className="border-b border-line hover:bg-surface-2">
                  <td className="px-3 py-2"><Link href={`/app/models/${m.name}`} className="font-medium text-accent hover:underline">{m.name}</Link><p className="text-xs text-ink-3 max-w-[260px] truncate">{m.task}</p></td>
                  <td className="px-3 py-2">{locale === "en" ? MODULE_LABELS[m.module]?.[1] ?? m.module : MODULE_LABELS[m.module]?.[0] ?? m.module}</td>
                  <td className="px-3 py-2">{m.production ? <span>v{m.production.version} <span className="text-xs text-ink-3">· {m.production.algorithm}</span></span> : <Badge tone="medium">{L("non déployé", "not deployed")}</Badge>}</td>
                  <td className="px-3 py-2 num text-ink-2">{fmtH(m.production?.headline ?? null)}</td>
                  <td className="px-3 py-2 num">{m.live ? fmtNumber(m.live.inferences, locale) : "—"}</td>
                  <td className="px-3 py-2">{m.drift?.status ? <Badge tone={driftTone(m.drift.status)}>{m.drift.status}{m.drift.max_psi != null ? ` · ${m.drift.max_psi.toFixed(3)}` : ""}</Badge> : "—"}</td>
                  <td className="px-3 py-2 text-xs">{m.latest ? <>v{m.latest.version} · <Badge tone={m.latest.status === "candidate" ? "medium" : m.latest.status === "rejected" ? "high" : "neutral"}>{m.latest.status}</Badge> · {fmtDate(m.latest.created_at, locale)}</> : "—"}</td>
                </tr>))}</tbody>
            </table></div>
          </Panel>
        </>
      )}
    </div>
  );
}
