"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Briefcase, Search, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { NetworkGraph, type NetEdge, type NetNode } from "@/components/network-graph";
import { BarsChart } from "@/components/ui/charts";
import { ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { useToast } from "@/components/ui/overlay";
import { Badge, Button, Field, Select, Textarea } from "@/components/ui/primitives";
import { RiskBadge, RiskGauge, type Band } from "@/components/ui/risk";
import { ApiError, get, patch, post } from "@/lib/api";
import { TYPOLOGY } from "@/lib/domain/aml";
import { fmtDate, fmtMoney, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useCan } from "@/lib/session";

type Side = { account_id: string | null; iban: string | null; name?: string | null; internal: boolean };
type Detail = {
  id: string; score: number; risk_band: Band; status: string; typologies: string[]; created_at: string; case_id: string | null; model_version: string;
  rule_hits: { rule: string; typology: string; weight: number; detail: string; transfers: number }[];
  model_scores: { policy: string; ml_probability: number; ml_percentile: number; rule_score: number; top_factors: { feature: string; label: string; value: number; contribution: number }[] };
  account: { id: string; iban: string; type: string; opened_at: string; balance: number; transfers: number; volume: number };
  customer: { id: string; name: string; ref: string; type: string; segment: string; risk_rating: string; kyc_status: string; pep: boolean; occupation: string | null; annual_income: number | null; is_demo: boolean };
  evidence: { id: string; ts: string; amount: number; channel: string; direction: string; reference: string | null; from: Side; to: Side; counterparty_country: string | null }[];
  simulation_label?: { typologies: Record<string, number>; note: string };
  disclaimer: string;
};

export default function AmlAlertPage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useI18n();
  const L = useL();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const [hops, setHops] = useState(2);
  const [focus, setFocus] = useState<string | null>(null);
  const [selected, setSelected] = useState<NetNode | null>(null);
  const [note, setNote] = useState("");
  const q = useQuery({ queryKey: ["aml", "alert", id], queryFn: () => get<Detail>(`/aml/alerts/${id}`) });
  const accountId = focus ?? q.data?.account.id;
  const net = useQuery({
    queryKey: ["aml", "network", accountId, hops], enabled: !!accountId,
    queryFn: () => get<{ nodes: NetNode[]; edges: NetEdge[]; truncated: boolean }>(`/aml/accounts/${accountId}/network?hops=${hops}&max_nodes=45`),
  });
  const onErr = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");
  const upd = useMutation({ mutationFn: (b: { status?: string; note?: string; assign_to_me?: boolean }) => patch(`/aml/alerts/${id}`, b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["aml"] }); toast(L("Alerte mise à jour", "Alert updated")); }, onError: onErr });
  const openCase = useMutation({ mutationFn: () => post<{ case_id: string }>(`/aml/alerts/${id}/case`),
    onSuccess: (r) => router.push(`/app/cases/${r.case_id}`), onError: onErr });

  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const a = q.data;
  const closed = a.status.startsWith("closed");
  const label = (s: Side) => s.internal ? s.iban : (s.name ?? s.iban ?? L("Espèces / inconnu", "Cash / unknown"));
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/aml" className="inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink w-fit"><ArrowLeft className="size-3.5" />{t("nav.aml")}</Link>
      <PageHeader title={a.customer.name} subtitle={`${a.account.iban} · ${a.customer.ref} · ${L("alerte du", "alert of")} ${fmtDate(a.created_at, locale, true)}`}
        meta={<><RiskBadge band={a.risk_band} />{a.typologies.map((x) => <Badge key={x}>{TYPOLOGY[x]?.[locale] ?? x}</Badge>)}<Badge>{a.status}</Badge>{a.customer.is_demo && <Badge tone="demo">DEMO</Badge>}</>}
        actions={<>
          {can("aml.investigate") && !a.case_id && <Button variant="primary" icon={<Briefcase className="size-4" />} onClick={() => openCase.mutate()} loading={openCase.isPending}>{L("Constituer un dossier", "Build a case")}</Button>}
          {a.case_id && <Link href={`/app/cases/${a.case_id}`}><Button icon={<Briefcase className="size-4" />}>{L("Ouvrir le dossier", "Open case")}</Button></Link>}
        </>} />
      <Notice tone="warn">{a.disclaimer}</Notice>

      <Panel title={L("Réseau de relations", "Relationship network")}
        description={L("Flux agrégés autour du compte. Trait plein rouge : virements qui justifient l'alerte. Cliquez un compte pour recentrer l'exploration.", "Aggregated flows around the account. Solid red: transfers that justify the alert. Click an account to re-centre the exploration.")}
        actions={<div className="flex items-center gap-2">
          {focus && <Button size="sm" variant="ghost" onClick={() => { setFocus(null); setSelected(null); }}>{L("Revenir au compte alerté", "Back to alerted account")}</Button>}
          <Select className="h-8 w-28" value={hops} onChange={(e) => setHops(Number(e.target.value))} aria-label={L("Profondeur", "Depth")}>
            <option value={1}>1 {L("saut", "hop")}</option><option value={2}>2 {L("sauts", "hops")}</option><option value={3}>3 {L("sauts", "hops")}</option>
          </Select></div>}>
        {net.error ? <ErrorState error={net.error} /> : !net.data ? <LoadingBlock /> : (
          <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
            <NetworkGraph nodes={net.data.nodes} edges={net.data.edges} onSelect={setSelected} />
            <div className="text-sm">
              {selected ? (
                <div className="flex flex-col gap-3">
                  <p className="font-medium">{selected.label}</p>
                  <KeyValue rows={[
                    [L("Type", "Type"), selected.kind === "account" ? L("Compte de la banque", "Bank account") : L("Contrepartie externe", "External counterparty")],
                    ["IBAN", <span key="i" className="num text-xs">{selected.iban ?? "—"}</span>],
                    [L("Rang modèle", "Model rank"), selected.ml_percentile != null ? fmtPct(selected.ml_percentile, locale) : "—"],
                    [L("Communauté", "Community"), selected.community ?? "—"],
                    [L("Typologies", "Typologies"), (selected.typologies ?? []).map((x) => TYPOLOGY[x]?.[locale] ?? x).join(", ") || "—"],
                  ]} />
                  {selected.kind === "account" && (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" icon={<Search className="size-3.5" />} onClick={() => setFocus(selected.id)}>{L("Explorer depuis ce compte", "Explore from this account")}</Button>
                      {selected.alert_id && selected.alert_id !== a.id && <Link href={`/app/aml/alerts/${selected.alert_id}`}><Button size="sm">{L("Voir son alerte", "View its alert")}</Button></Link>}
                    </div>
                  )}
                </div>
              ) : <p className="text-ink-3">{L("Sélectionnez un nœud pour afficher ses caractéristiques.", "Select a node to show its details.")}</p>}
              {net.data.truncated && <p className="mt-4 text-xs text-ink-3">{L("Graphe limité aux contreparties les plus significatives en montant.", "Graph limited to the most significant counterparties by amount.")}</p>}
            </div>
          </div>
        )}
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <div className="flex flex-col gap-6">
          <Panel title={L("Règles déclenchées", "Triggered rules")}>
            {a.rule_hits.length === 0 ? <p className="text-sm text-ink-3">{L("Aucune règle : alerte issue du rang du modèle de graphe (top 0,5 %).", "No rule: alert raised by the graph model rank (top 0.5%).")}</p> : (
              <ul className="divide-y divide-line">
                {a.rule_hits.map((h, i) => (
                  <li key={i} className="py-2.5 flex items-start gap-3">
                    <Badge tone="high">{h.rule}</Badge>
                    <div className="text-sm"><p className="text-ink">{h.detail}</p><p className="text-xs text-ink-3">{TYPOLOGY[h.typology]?.[locale]} · {L("poids", "weight")} {h.weight} · {h.transfers} {L("opérations justificatives", "supporting transactions")}</p></div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title={L("Opérations justificatives", "Supporting transactions")} flush>
            <div className="overflow-x-auto max-h-[440px] scroll-thin">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-surface"><tr className="border-b border-line text-left text-[12.5px] text-ink-3">
                  <th className="px-3 py-2 font-medium">{t("common.date")}</th><th className="px-3 py-2 font-medium">{L("De", "From")}</th><th className="px-3 py-2 font-medium">{L("Vers", "To")}</th>
                  <th className="px-3 py-2 font-medium">{L("Canal", "Channel")}</th><th className="px-3 py-2 font-medium text-right">{t("common.amount")}</th></tr></thead>
                <tbody>
                  {a.evidence.map((e) => (
                    <tr key={e.id} className="border-b border-line">
                      <td className="px-3 py-1.5 whitespace-nowrap text-ink-2">{fmtDate(e.ts, locale, true)}</td>
                      <td className="px-3 py-1.5 text-xs num">{label(e.from)}</td>
                      <td className="px-3 py-1.5 text-xs num">{label(e.to)}{e.counterparty_country && !e.to.internal ? ` (${e.counterparty_country})` : ""}</td>
                      <td className="px-3 py-1.5 text-ink-2">{e.channel}{e.reference ? ` · ${e.reference}` : ""}</td>
                      <td className="px-3 py-1.5 text-right num">{fmtMoney(e.amount, locale)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
        <div className="flex flex-col gap-6">
          <Panel title={L("Score et composantes", "Score and components")}>
            <div className="flex items-center gap-4 mb-3">
              <RiskGauge score={a.score} band={a.risk_band} />
              <div><p className="num text-2xl font-semibold">{a.score.toFixed(3)}</p><p className="text-xs text-ink-3">{a.model_scores.policy}</p></div>
            </div>
            <KeyValue rows={[
              [L("Score des règles", "Rule score"), <span key="r" className="num">{a.model_scores.rule_score.toFixed(3)}</span>],
              [L("Rang du modèle de graphe", "Graph model rank"), <span key="m" className="num">{fmtPct(a.model_scores.ml_percentile, locale)}</span>],
              [L("Version du modèle", "Model version"), a.model_version],
            ]} />
            {a.model_scores.top_factors.length > 0 && (
              <div className="mt-4"><BarsChart horizontal height={190} title={L("Facteurs du modèle (SHAP)", "Model factors (SHAP)")}
                data={a.model_scores.top_factors.map((f) => ({ f: f.label, c: f.contribution }))} xKey="f"
                series={[{ key: "c", label: L("Contribution", "Contribution"), color: "var(--risk-high)" }]} fmt={(v) => v.toFixed(2)} /></div>
            )}
          </Panel>
          <Panel title={t("common.customer")}>
            <KeyValue rows={[[L("Type", "Type"), a.customer.type], [L("Segment", "Segment"), a.customer.segment], [L("Profession", "Occupation"), a.customer.occupation ?? "—"],
              [L("Revenu annuel déclaré", "Declared annual income"), a.customer.annual_income ? fmtMoney(a.customer.annual_income, locale, "EUR", 0) : "—"],
              [L("Notation risque", "Risk rating"), a.customer.risk_rating], ["KYC", a.customer.kyc_status], ["PPE / PEP", a.customer.pep ? t("common.yes") : t("common.no")],
              [L("Volume du compte", "Account volume"), `${a.account.transfers} · ${fmtMoney(a.account.volume, locale, "EUR", 0)}`]]} />
          </Panel>
          {can("aml.investigate") && !closed && (
            <Panel title={L("Qualification", "Qualification")}>
              <div className="flex flex-col gap-3">
                <Button size="sm" onClick={() => upd.mutate({ status: "investigating", assign_to_me: true })}>{L("Prendre en charge", "Take ownership")}</Button>
                <Field label={L("Justification (20 caractères minimum pour clôturer)", "Justification (20+ characters to close)")} htmlFor="n">
                  <Textarea id="n" value={note} onChange={(e) => setNote(e.target.value)} />
                </Field>
                <Button size="sm" icon={<XCircle className="size-4" />} disabled={note.trim().length < 20} onClick={() => upd.mutate({ status: "closed_no_action", note })}>
                  {L("Clôturer sans suite", "Close with no action")}
                </Button>
              </div>
            </Panel>
          )}
          {a.simulation_label && <Notice tone="demo" title={L("Vérité terrain DEMO", "DEMO ground truth")}>
            {Object.keys(a.simulation_label.typologies).length ? Object.entries(a.simulation_label.typologies).map(([k, v]) => `${TYPOLOGY[k]?.[locale] ?? k} (${v})`).join(", ") : L("Aucune typologie injectée dans ces opérations.", "No injected typology in these transactions.")}. {a.simulation_label.note}
          </Notice>}
        </div>
      </div>
    </div>
  );
}
