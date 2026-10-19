"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { BarsChart } from "@/components/ui/charts";
import { ErrorState, KeyValue, Notice, PageHeader, Panel } from "@/components/ui/layout";
import { Button, Field, Input, Select, Switch } from "@/components/ui/primitives";
import { RiskBadge, RiskGauge, type Band } from "@/components/ui/risk";
import { get, post, qs } from "@/lib/api";
import { fmtNumber } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe } from "@/lib/session";

type CardRow = { id: string; card_ref: string; customer: string; customer_ref: string; city: string; home_region: string };
type Result = {
  score: number; band: Band; threshold: number; model_version: string; latency_ms: number; history_used: number;
  reasons: { feature: string; label: string; value: number | string | null; contribution: number }[];
  features: Record<string, number>; transaction_id: string | null; alert_id: string | null;
};

const MCC = [
  [5411, "Supermarché / Grocery"], [5812, "Restaurant"], [5541, "Station-service / Fuel"], [5732, "Électronique / Electronics"],
  [5944, "Bijouterie / Jewelry"], [6011, "Retrait DAB / ATM"], [4829, "Transfert d'argent / Money transfer"], [7995, "Jeux / Gambling"],
  [5815, "Services numériques / Digital goods"], [4511, "Aérien / Airline"], [7011, "Hôtel / Hotel"], [5999, "Divers / Misc retail"],
] as const;

export default function Simulator() {
  const { t, locale } = useI18n();
  const L = useL();
  const { data: me } = useMe();
  const [cardQ, setCardQ] = useState("");
  const cards = useQuery({ queryKey: ["fraud", "cards", cardQ], queryFn: () => get<CardRow[]>(`/fraud/cards${qs({ q: cardQ, limit: 30 })}`) });
  const [form, setForm] = useState({ card_id: "", amount: "1450", mcc: "5732", channel: "Online Transaction", merchant_city: "Paris", merchant_region: "Île-de-France", errors: "", persist: false });
  const run = useMutation({
    mutationFn: () => post<Result>("/fraud/simulate", {
      card_id: form.card_id || cards.data?.[0]?.id, amount: Number(form.amount), mcc: Number(form.mcc), channel: form.channel,
      merchant_city: form.merchant_city, merchant_region: form.merchant_region || null, errors: form.errors || null, persist: form.persist,
      merchant_name: L("Commerçant simulé (DEMO)", "Simulated merchant (DEMO)"),
    }),
  });
  const set = (k: keyof typeof form, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }));
  const submit = (e: FormEvent) => { e.preventDefault(); run.mutate(); };
  const r = run.data;
  return (
    <div className="flex flex-col gap-6">
      <Link href="/app/fraud" className="inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink w-fit"><ArrowLeft className="size-3.5" />{t("nav.fraud")}</Link>
      <PageHeader title={t("fraud.sim.title")} subtitle={t("fraud.sim.subtitle")} />
      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <Panel title={L("Transaction à scorer", "Transaction to score")}>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <Field label={L("Rechercher une carte", "Find a card")} htmlFor="cq">
              <Input id="cq" value={cardQ} onChange={(e) => setCardQ(e.target.value)} placeholder={L("Nom, référence client…", "Name, customer reference…")} />
            </Field>
            <Field label={t("fraud.sim.card")} htmlFor="card">
              <Select id="card" value={form.card_id} onChange={(e) => set("card_id", e.target.value)}>
                {(cards.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.customer} · {c.card_ref} · {c.city}</option>)}
              </Select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={`${t("common.amount")} (EUR)`} htmlFor="amt"><Input id="amt" type="number" step="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} className="num" /></Field>
              <Field label={t("fraud.sim.channel")} htmlFor="ch">
                <Select id="ch" value={form.channel} onChange={(e) => set("channel", e.target.value)}>
                  <option value="Chip Transaction">{L("Puce", "Chip")}</option><option value="Swipe Transaction">{L("Piste", "Swipe")}</option><option value="Online Transaction">{L("En ligne", "Online")}</option>
                </Select>
              </Field>
            </div>
            <Field label={t("fraud.sim.mcc")} htmlFor="mcc">
              <Select id="mcc" value={form.mcc} onChange={(e) => set("mcc", e.target.value)}>
                {MCC.map(([code, label]) => <option key={code} value={code}>{code} · {label}</option>)}
              </Select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("fraud.sim.city")} htmlFor="city"><Input id="city" value={form.merchant_city} onChange={(e) => set("merchant_city", e.target.value)} disabled={form.channel === "Online Transaction"} /></Field>
              <Field label={t("fraud.sim.region")} htmlFor="reg" hint={L("Région française, ou nom de pays pour l'étranger", "French region, or country name abroad")}>
                <Input id="reg" value={form.merchant_region} onChange={(e) => set("merchant_region", e.target.value)} disabled={form.channel === "Online Transaction"} />
              </Field>
            </div>
            <Field label={t("fraud.sim.errors")} htmlFor="err">
              <Select id="err" value={form.errors} onChange={(e) => set("errors", e.target.value)}>
                <option value="">{t("common.none")}</option><option>Bad PIN</option><option>Bad CVV</option><option>Insufficient Balance</option><option>Technical Glitch</option>
              </Select>
            </Field>
            {me?.org.kind === "demo" && (
              <label className="flex items-center gap-3 text-sm text-ink-2"><Switch checked={form.persist} onChange={(v) => set("persist", v)} label={t("fraud.sim.persist")} />{t("fraud.sim.persist")}</label>
            )}
            <Button type="submit" variant="primary" loading={run.isPending}>{t("fraud.sim.run")}</Button>
          </form>
        </Panel>
        <Panel title={t("fraud.sim.result")}>
          {run.error ? <ErrorState error={run.error} /> : !r ? (
            <p className="text-sm text-ink-3">{L("Le score, les facteurs explicatifs et les variables calculées s'afficheront ici.", "The score, explanatory factors and computed features will appear here.")}</p>
          ) : (
            <div className="flex flex-col gap-5">
              <div className="flex items-center gap-4">
                <RiskGauge score={r.score} band={r.band} />
                <div>
                  <p className="num text-3xl font-semibold">{r.score.toFixed(3)}</p>
                  <p className="text-sm text-ink-3">{r.band ? <RiskBadge band={r.band} /> : t("fraud.sim.noAlert")} · {L("seuil", "threshold")} {r.threshold.toFixed(3)} · {r.model_version} · {fmtNumber(r.latency_ms, locale, 1)} ms</p>
                </div>
                {r.alert_id && <Link href={`/app/fraud/alerts/${r.alert_id}`} className="ml-auto"><Button size="sm">{L("Voir l'alerte créée", "View created alert")}</Button></Link>}
              </div>
              {r.reasons.length > 0 && (
                <BarsChart horizontal height={Math.max(150, r.reasons.length * 40)} title={t("fraud.alert.why")}
                  data={r.reasons.map((x) => ({ f: x.label, c: x.contribution }))} xKey="f" series={[{ key: "c", label: L("Contribution", "Contribution"), color: "var(--risk-high)" }]} fmt={(v) => v.toFixed(2)} />
              )}
              <div>
                <p className="text-[13px] font-medium text-ink-2 mb-2">{L(`Variables calculées (historique : ${r.history_used} transactions sur 31 jours)`, `Computed features (history: ${r.history_used} transactions over 31 days)`)}</p>
                <KeyValue className="text-[13px]" rows={Object.entries(r.features).slice(0, 24).map(([k, v]) => [k, <span key={k} className="num">{typeof v === "number" ? fmtNumber(v, locale, 2) : String(v)}</span>])} />
              </div>
              <Notice>{L("Le simulateur utilise exactement le même calcul de variables et le même modèle déployé que le scoring en production.", "The simulator uses exactly the same feature computation and deployed model as production scoring.")}</Notice>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
