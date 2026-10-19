"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Search } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { BarsChart, TimeSeriesChart } from "@/components/ui/charts";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState, ErrorState, KeyValue, LoadingBlock, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Tabs, TabPanel } from "@/components/ui/overlay";
import { Badge, Button, Input, Select } from "@/components/ui/primitives";
import { DemoTag } from "@/components/ui/risk";
import { get, qs } from "@/lib/api";
import { fmtDate, fmtNumber, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe } from "@/lib/session";

type Overview = {
  segments: { segment_id: number; label: string; customers: number; model_version: string; profile: Record<string, number> }[];
  topics: { group: string; messages: number; avg_confidence: number }[];
  weekly_messages: { week: string; messages: number; complaints: number }[];
  complaint_share: number | null; messages: number;
};
type Cust = { id: string; name: string; ref: string; type: string; segment: string; city: string; risk_rating: string; kyc_status: string; behaviour_segment: string | null; is_demo: boolean };
type Msg = { id: string; received_at: string; channel: string; text: string; customer: string | null; topic: string | null; group: string | null; confidence: number | null; complaint: boolean };
type Hit = { id: string; text: string; customer: string | null; topic: string; group: string; received_at: string; similarity: number };
type Campaign = { source: string; version: number; metrics: {
  split: { base_rate: Record<string, number> }; comparison: { model: string; test: { roc_auc: number; pr_auc: number } }[];
  lift_test: { decile: number; conversion: number; lift: number }[]; global_importance: { feature: string; mean_abs_shap: number }[];
  campaign_analysis: Record<string, { value: string; contacts: number; conversion: number }[]>; reference_random_split_auc_all_features: number };
  model_card: { limitations: string[] } };

const GROUP_LABEL: Record<string, { fr: string; en: string }> = {
  card_lost_or_stolen: { fr: "Carte perdue ou volée", en: "Lost or stolen card" }, card_payment_issue: { fr: "Problème de paiement carte", en: "Card payment issue" },
  unrecognised_transaction: { fr: "Opération non reconnue", en: "Unrecognised transaction" }, transfer_issue: { fr: "Virement", en: "Transfer" },
  top_up: { fr: "Rechargement", en: "Top-up" }, cash_atm: { fr: "Retrait / DAB", en: "Cash / ATM" }, fees_and_exchange: { fr: "Frais et change", en: "Fees and exchange" },
  refund: { fr: "Remboursement", en: "Refund" }, pin_and_security: { fr: "Code PIN et sécurité", en: "PIN and security" },
  identity_and_account: { fr: "Identité et compte", en: "Identity and account" }, other: { fr: "Autre", en: "Other" },
};

export default function CustomersPage() {
  const { t, locale } = useI18n();
  const L = useL();
  const { data: me } = useMe();
  const [tab, setTab] = useState("customers");
  const [q, setQ] = useState("");
  const [seg, setSeg] = useState("");
  const [page, setPage] = useState(1);
  const [group, setGroup] = useState("");
  const [complaints, setComplaints] = useState(false);
  const [mpage, setMpage] = useState(1);
  const [sq, setSq] = useState("");
  const [search, setSearch] = useState("");
  const ov = useQuery({ queryKey: ["ci", "overview"], queryFn: () => get<Overview>("/customer-intelligence/overview") });
  const custs = useQuery({ queryKey: ["customers", q, seg, page], placeholderData: (p) => p,
    queryFn: () => get<{ total: number; items: Cust[] }>(`/customers${qs({ q, segment: seg, page })}`) });
  const msgs = useQuery({ queryKey: ["ci", "msgs", group, complaints, mpage], enabled: tab === "messages", placeholderData: (p) => p,
    queryFn: () => get<{ total: number; items: Msg[] }>(`/customer-intelligence/messages${qs({ group, complaints_only: complaints || undefined, page: mpage })}`) });
  const hits = useQuery({ queryKey: ["ci", "search", search], enabled: search.length >= 3, queryFn: () => get<Hit[]>(`/customer-intelligence/search${qs({ q: search })}`) });
  const camp = useQuery({ queryKey: ["ci", "campaigns"], enabled: tab === "campaigns", queryFn: () => get<Campaign>("/customer-intelligence/campaigns") });
  const g = (k: string | null) => (k ? GROUP_LABEL[k]?.[locale] ?? k : "—");
  const custCols = useMemo<ColumnDef<Cust, unknown>[]>(() => [
    { accessorKey: "name", header: L("Nom", "Name"), cell: ({ row }) => <div><p>{row.original.name}</p><p className="text-xs text-ink-3">{row.original.ref}</p></div> },
    { accessorKey: "segment", header: L("Segment commercial", "Commercial segment") },
    { accessorKey: "behaviour_segment", header: L("Segment comportemental", "Behavioural segment"), cell: ({ getValue }) => getValue() ? <Badge tone="accent">{String(getValue())}</Badge> : "—" },
    { accessorKey: "city", header: L("Ville", "City") },
    { accessorKey: "risk_rating", header: L("Risque", "Risk") },
    { accessorKey: "kyc_status", header: "KYC" },
  ], [L]);
  const msgCols = useMemo<ColumnDef<Msg, unknown>[]>(() => [
    { accessorKey: "received_at", header: t("common.date"), cell: ({ getValue }) => <span className="whitespace-nowrap text-ink-2">{fmtDate(String(getValue()), locale, true)}</span> },
    { accessorKey: "text", header: L("Message", "Message"), cell: ({ getValue }) => <span className="block max-w-xl">{String(getValue())}</span> },
    { accessorKey: "group", header: L("Thématique", "Topic"), cell: ({ row }) => <div><p>{g(row.original.group)}</p><p className="text-xs text-ink-3">{row.original.topic} · {row.original.confidence?.toFixed(2)}</p></div> },
    { accessorKey: "complaint", header: L("Réclamation", "Complaint"), cell: ({ getValue }) => getValue() ? <Badge tone="high">{L("Oui", "Yes")}</Badge> : "—" },
  ], [t, L, locale]); // eslint-disable-line react-hooks/exhaustive-deps
  const o = ov.data;
  const c = camp.data?.metrics;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("nav.customers")} subtitle={L("Segmentation comportementale, motifs de contact et analyse de campagnes.", "Behavioural segmentation, contact reasons and campaign analytics.")} meta={me?.org.kind === "demo" ? <DemoTag /> : undefined} />
      <Tabs value={tab} onValueChange={setTab} items={[{ value: "customers", label: L("Clients & segments", "Customers & segments") }, { value: "messages", label: L("Messages & réclamations", "Messages & complaints") }, { value: "campaigns", label: L("Campagnes (jeu public)", "Campaigns (public dataset)") }]}>
        <TabPanel value="customers" className="pt-4 flex flex-col gap-6">
          {ov.error ? <ErrorState error={ov.error} /> : !o ? <LoadingBlock /> : o.segments.length === 0 ? <Notice>{L("Segmentation non calculée pour cette organisation.", "Segmentation not computed for this organisation.")}</Notice> : (
            <Panel title={L("Segments comportementaux", "Behavioural segments")} description={`K-Means · ${o.segments[0]?.model_version} · ${L("profil médian par segment", "median profile per segment")}`} flush>
              <div className="overflow-x-auto"><table className="w-full text-sm">
                <thead><tr className="border-b border-line text-left text-[12.5px] text-ink-3">
                  {[L("Segment", "Segment"), L("Clients", "Customers"), L("Opérations/mois", "Tx/month"), L("Dépense/mois", "Spend/month"), L("Panier moyen", "Avg ticket"), L("Part en ligne", "Online share"), L("Revenu (k€)", "Income (k€)"), L("Âge", "Age")].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
                </tr></thead>
                <tbody>{o.segments.map((s) => (
                  <tr key={s.segment_id} className="border-b border-line cursor-pointer hover:bg-surface-2" onClick={() => { setSeg(String(s.segment_id)); setPage(1); }}>
                    <td className="px-3 py-2 font-medium">{s.label}</td><td className="px-3 py-2 num">{fmtNumber(s.customers, locale)}</td>
                    <td className="px-3 py-2 num">{fmtNumber(s.profile.tx_per_month, locale, 1)}</td><td className="px-3 py-2 num">{fmtNumber(s.profile.spend_per_month, locale, 0)} €</td>
                    <td className="px-3 py-2 num">{fmtNumber(s.profile.avg_ticket, locale, 0)} €</td><td className="px-3 py-2 num">{fmtPct(s.profile.online_share, locale, 0)}</td>
                    <td className="px-3 py-2 num">{fmtNumber(s.profile.income_k, locale, 0)}</td><td className="px-3 py-2 num">{fmtNumber(s.profile.age, locale, 0)}</td>
                  </tr>))}</tbody>
              </table></div>
            </Panel>
          )}
          <Panel title={L("Clients", "Customers")} flush actions={<div className="flex gap-2">
            <Input className="h-8 w-52" placeholder={L("Nom, référence, ville…", "Name, reference, city…")} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label={t("common.search")} />
            <Select className="h-8 w-52" value={seg} onChange={(e) => { setSeg(e.target.value); setPage(1); }} aria-label={L("Segment", "Segment")}>
              <option value="">{L("Tous segments", "All segments")}</option>{o?.segments.map((s) => <option key={s.segment_id} value={s.segment_id}>{s.label}</option>)}
            </Select></div>}>
            {custs.error ? <ErrorState error={custs.error} /> : <DataTable columns={custCols} data={custs.data?.items} loading={custs.isLoading} total={custs.data?.total} page={page} onPageChange={setPage} rowHref={(r) => `/app/customers/${r.id}`} />}
          </Panel>
        </TabPanel>
        <TabPanel value="messages" className="pt-4 flex flex-col gap-6">
          {o && (
            <div className="grid gap-6 lg:grid-cols-2">
              <Panel title={L("Messages par thématique", "Messages by topic")} description={L(`${fmtNumber(o.messages, locale)} messages · réclamations ${o.complaint_share == null ? "—" : fmtPct(o.complaint_share, locale)}`, `${fmtNumber(o.messages, locale)} messages · complaints ${o.complaint_share == null ? "—" : fmtPct(o.complaint_share, locale)}`)}>
                <BarsChart horizontal height={300} data={o.topics.map((x) => ({ t: g(x.group), n: x.messages }))} xKey="t" series={[{ key: "n", label: L("Messages", "Messages") }]} />
              </Panel>
              <Panel title={L("Tendance hebdomadaire", "Weekly trend")}>
                <TimeSeriesChart height={300} data={o.weekly_messages.map((w) => ({ ...w, w: w.week.slice(5) }))} xKey="w"
                  series={[{ key: "messages", label: L("Messages", "Messages") }, { key: "complaints", label: L("Réclamations", "Complaints") }]} />
              </Panel>
            </div>
          )}
          <Panel title={L("Recherche sémantique", "Semantic search")} description={L("Recherche par similarité (embeddings LSA stockés dans pgvector).", "Similarity search (LSA embeddings stored in pgvector).")}>
            <form className="flex gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); setSearch(sq.trim()); }}>
              <Input value={sq} onChange={(e) => setSq(e.target.value)} placeholder={L("ex. my card payment was refused abroad", "e.g. my card payment was refused abroad")} aria-label={L("Requête", "Query")} />
              <Button type="submit" icon={<Search className="size-4" />}>{t("common.search")}</Button>
            </form>
            {hits.data && (hits.data.length === 0 ? <EmptyState title={L("Aucun message proche", "No similar message")} /> : (
              <ul className="mt-4 divide-y divide-line">{hits.data.map((h) => (
                <li key={h.id} className="py-2.5 text-sm flex gap-3"><span className="num text-xs text-ink-3 w-12 shrink-0">{h.similarity.toFixed(2)}</span>
                  <div><p>{h.text}</p><p className="text-xs text-ink-3">{h.customer} · {g(h.group)} · {fmtDate(h.received_at, locale)}</p></div></li>))}</ul>
            ))}
            <p className="mt-3 text-xs text-ink-3">{L("Les messages DEMO sont rédigés en anglais : le classifieur est entraîné sur BANKING77 (anglais).", "DEMO messages are written in English: the classifier is trained on BANKING77 (English).")}</p>
          </Panel>
          <Panel title={L("Messages", "Messages")} flush actions={<div className="flex gap-2 items-center">
            <Select className="h-8 w-52" value={group} onChange={(e) => { setGroup(e.target.value); setMpage(1); }} aria-label={L("Thématique", "Topic")}>
              <option value="">{L("Toutes thématiques", "All topics")}</option>{Object.entries(GROUP_LABEL).map(([k, v]) => <option key={k} value={k}>{v[locale]}</option>)}
            </Select>
            <label className="flex items-center gap-1.5 text-[13px] text-ink-2"><input type="checkbox" checked={complaints} onChange={(e) => { setComplaints(e.target.checked); setMpage(1); }} />{L("Réclamations", "Complaints")}</label></div>}>
            {msgs.error ? <ErrorState error={msgs.error} /> : <DataTable columns={msgCols} data={msgs.data?.items} loading={msgs.isLoading} total={msgs.data?.total} page={mpage} onPageChange={setMpage} />}
          </Panel>
        </TabPanel>
        <TabPanel value="campaigns" className="pt-4 flex flex-col gap-6">
          {camp.error ? <ErrorState error={camp.error} /> : !c ? <LoadingBlock /> : (
            <>
              <Notice title={L("Jeu de données public", "Public dataset")}>{camp.data?.source}</Notice>
              <div className="grid gap-6 lg:grid-cols-2">
                <Panel title={L("Performance (découpage chronologique)", "Performance (chronological split)")}>
                  <KeyValue rows={[
                    ...c.comparison.map((m) => [m.model, `AUC ${m.test.roc_auc.toFixed(3)} · PR-AUC ${m.test.pr_auc.toFixed(3)}`] as [string, string]),
                    [L("Référence découpage aléatoire (optimiste)", "Random-split reference (optimistic)"), `AUC ${c.reference_random_split_auc_all_features.toFixed(3)}`],
                    [L("Taux de souscription train/val/test", "Subscription rate train/val/test"), `${fmtPct(c.split.base_rate.train, locale)} / ${fmtPct(c.split.base_rate.val, locale)} / ${fmtPct(c.split.base_rate.test, locale)}`],
                  ]} />
                  <ul className="mt-3 list-disc pl-5 text-xs text-ink-3">{camp.data?.model_card.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
                </Panel>
                <Panel title={L("Lift par décile (test)", "Lift by decile (test)")}>
                  <BarsChart data={c.lift_test.map((d) => ({ d: `${d.decile * 10}%`, lift: d.lift }))} xKey="d" series={[{ key: "lift", label: "Lift" }]} height={220} fmt={(v) => v.toFixed(2)} />
                </Panel>
                {(["poutcome", "contact", "month", "campaign_contacts"] as const).map((k) => (
                  <Panel key={k} title={`${L("Conversion par", "Conversion by")} ${k}`}>
                    <BarsChart data={(c.campaign_analysis[k] ?? []).map((r) => ({ v: r.value, conv: r.conversion }))} xKey="v" series={[{ key: "conv", label: L("Taux de souscription", "Subscription rate") }]} height={200} fmt={(v) => fmtPct(v, locale, 0)} />
                  </Panel>
                ))}
              </div>
            </>
          )}
        </TabPanel>
      </Tabs>
    </div>
  );
}
