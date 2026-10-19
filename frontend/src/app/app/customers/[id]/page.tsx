"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ErrorState, KeyValue, LoadingBlock, PageHeader, Panel } from "@/components/ui/layout";
import { Badge } from "@/components/ui/primitives";
import { get } from "@/lib/api";
import { fmtDate, fmtMoney, fmtPct } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

type C360 = {
  id: string; name: string; ref: string; type: string; segment: string; city: string; postal_code: string; occupation: string | null;
  annual_income: number | null; birth_year: number | null; risk_rating: string; kyc_status: string; pep: boolean; onboarded_at: string; is_demo: boolean;
  accounts: { id: string; iban: string; type: string; balance: number; status: string }[];
  cards: { id: string; ref: string; brand: string; type: string; status: string }[];
  behaviour_segment: { label: string; features: Record<string, number>; model_version: string } | null;
  messages: { id: string; received_at: string; channel: string; text: string; topic: string | null; group: string | null; complaint: boolean }[];
  recent_card_transactions?: { id: string; ts: string; amount: number; merchant: string; mcc: number; risk_score: number | null }[];
  fraud_alerts?: number; aml_alerts?: number;
  credit_applications?: { id: string; reference: string; pd: number | null; grade: string | null; status: string }[];
};

export default function Customer360() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useI18n();
  const L = useL();
  const q = useQuery({ queryKey: ["customer", id], queryFn: () => get<C360>(`/customers/${id}`) });
  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorState error={q.error} />;
  const c = q.data;
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/customers" className="inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink w-fit"><ArrowLeft className="size-3.5" />{t("nav.customers")}</Link>
      <PageHeader title={c.name} subtitle={`${c.ref} · ${c.city} ${c.postal_code ?? ""} · ${L("client depuis", "customer since")} ${fmtDate(c.onboarded_at, locale)}`}
        meta={<><Badge>{c.type}</Badge><Badge>{c.segment}</Badge>{c.behaviour_segment && <Badge tone="accent">{c.behaviour_segment.label}</Badge>}{c.pep && <Badge tone="high">PPE</Badge>}{c.is_demo && <Badge tone="demo">DEMO</Badge>}</>} />
      <div className="grid gap-6 lg:grid-cols-3">
        <Panel title={L("Profil", "Profile")}>
          <KeyValue rows={[[L("Profession", "Occupation"), c.occupation ?? "—"], [L("Revenu annuel déclaré", "Declared annual income"), c.annual_income ? fmtMoney(c.annual_income, locale, "EUR", 0) : "—"],
            [L("Année de naissance", "Birth year"), c.birth_year ?? "—"], [L("Notation risque", "Risk rating"), c.risk_rating], ["KYC", c.kyc_status],
            ...(c.fraud_alerts !== undefined ? [[L("Alertes fraude", "Fraud alerts"), String(c.fraud_alerts)] as [string, string]] : []),
            ...(c.aml_alerts !== undefined ? [[L("Alertes LCB-FT", "AML alerts"), String(c.aml_alerts)] as [string, string]] : [])]} />
        </Panel>
        <Panel title={L("Comptes et cartes", "Accounts and cards")}>
          <ul className="flex flex-col gap-2 text-sm">
            {c.accounts.map((a) => <li key={a.id} className="flex justify-between gap-3"><span className="num text-xs">{a.iban}</span><span className="num">{fmtMoney(a.balance, locale)}</span></li>)}
            {c.cards.map((k) => <li key={k.id} className="flex justify-between gap-3 text-ink-2"><span>{k.ref} · {k.brand} {k.type}</span><span>{k.status}</span></li>)}
          </ul>
        </Panel>
        <Panel title={L("Comportement (segmentation)", "Behaviour (segmentation)")}>
          {c.behaviour_segment ? <KeyValue rows={Object.entries(c.behaviour_segment.features).map(([k, v]) => [k, <span key={k} className="num">{k.includes("share") ? fmtPct(v, locale, 0) : v.toFixed(1)}</span>])} /> : <p className="text-sm text-ink-3">—</p>}
        </Panel>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        {c.recent_card_transactions && <Panel title={L("Dernières opérations carte", "Latest card transactions")} flush>
          <table className="w-full text-sm"><tbody>{c.recent_card_transactions.map((x) => (
            <tr key={x.id} className="border-b border-line"><td className="px-3 py-1.5 text-ink-2 whitespace-nowrap">{fmtDate(x.ts, locale, true)}</td><td className="px-3 py-1.5 truncate max-w-[220px]">{x.merchant}</td>
              <td className="px-3 py-1.5 text-right num">{fmtMoney(x.amount, locale)}</td><td className="px-3 py-1.5 text-right num text-ink-3">{x.risk_score?.toFixed(3) ?? "—"}</td></tr>))}</tbody></table>
        </Panel>}
        <Panel title={L("Messages du client", "Customer messages")}>
          {c.messages.length === 0 ? <p className="text-sm text-ink-3">{L("Aucun message.", "No messages.")}</p> : (
            <ul className="divide-y divide-line">{c.messages.map((m) => (
              <li key={m.id} className="py-2 text-sm"><p>{m.text}</p><p className="text-xs text-ink-3">{fmtDate(m.received_at, locale, true)} · {m.channel} · {m.group ?? "—"}{m.complaint ? ` · ${L("réclamation", "complaint")}` : ""}</p></li>))}</ul>
          )}
        </Panel>
        {c.credit_applications && c.credit_applications.length > 0 && <Panel title={L("Demandes de crédit", "Credit applications")}>
          <ul className="flex flex-col gap-1.5 text-sm">{c.credit_applications.map((a) => (
            <li key={a.id}><Link className="flex justify-between hover:text-accent" href={`/app/credit/applications/${a.id}`}><span className="num">{a.reference}</span><span>{a.pd == null ? "—" : `PD ${fmtPct(a.pd, locale)} · ${a.grade}`}</span><span className="text-ink-3">{a.status}</span></Link></li>))}</ul>
        </Panel>}
      </div>
    </div>
  );
}
