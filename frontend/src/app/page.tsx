"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { SiteShell } from "@/components/site/site-chrome";
import { get } from "@/lib/api";
import { MODULE_LABELS } from "@/lib/domain/models";
import { useI18n, useL } from "@/lib/i18n";

type Evidence = { models: { name: string; module: string; task: string; version: number; algorithm: string; headline: { label: string; value: number | string } | null }[];
  sources: { key: string; name: string; license: string; kind: string }[]; demo_enabled: boolean; notice: string };

const GROUPS: { fr: string; en: string; items: [string, string, string, string][] }[] = [
  { fr: "Détecter", en: "Detect", items: [
    ["Fraude carte", "Card fraud", "Score de chaque paiement, raisons du modèle, file d'alertes avec délais.", "Every payment scored, model reasons, alert queue with deadlines."],
    ["LCB-FT", "AML", "Règles de typologies et apprentissage sur graphe des contreparties.", "Typology rules and graph learning over counterparties."]] },
  { fr: "Évaluer", en: "Assess", items: [
    ["Risque de crédit", "Credit risk", "Probabilité de défaut calibrée, facteurs, simulations ; décision humaine.", "Calibrated probability of default, factors, what-ifs; human decision."],
    ["Documents & KYC", "Documents & KYC", "OCR, extraction avec positions, contrôles IBAN, SIREN, soldes, totaux.", "OCR, positioned extraction, IBAN, SIREN, balance and total checks."],
    ["Connaissance client", "Customer intelligence", "Thèmes des messages, réclamations, segments, appétence.", "Message topics, complaints, segments, propensity."],
    ["Prévisions", "Forecasting", "Huit modèles comparés en backtest, scénarios et intervalles d'incertitude.", "Eight models compared by backtest, scenarios and uncertainty intervals."]] },
  { fr: "Assister", en: "Assist", items: [
    ["Copilote financier", "Financial copilot", "Réponses citant leurs passages : rapports annuels publics et procédures internes.", "Answers citing their passages: public annual reports and internal procedures."],
    ["Agents IA", "AI agents", "Huit agents préparent l'analyse ; chaque action attend une approbation humaine.", "Eight agents prepare the analysis; every action awaits human approval."]] },
  { fr: "Gouverner", en: "Govern", items: [
    ["Plateforme de données", "Data platform", "Sources sous licence, couches bronze, silver, gold, qualité et lignage.", "Licensed sources, bronze, silver, gold layers, quality and lineage."],
    ["Gouvernance & MLOps", "Governance & MLOps", "Registre, revue à quatre yeux, déploiement, retour arrière, dérive, incidents.", "Registry, four-eyes review, deployment, rollback, drift, incidents."]] },
];

export default function LandingPage() {
  const { locale } = useI18n();
  const L = useL();
  const ev = useQuery({ queryKey: ["public", "evidence"], queryFn: () => get<Evidence>("/public/evidence"), retry: 1 });
  const modLabel = (m: string) => (locale === "en" ? MODULE_LABELS[m]?.[1] : MODULE_LABELS[m]?.[0]) ?? m;
  return (
    <SiteShell>
      <section className="mx-auto grid max-w-6xl gap-12 px-4 pt-16 pb-10 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:pt-24">
        <div>
          <h1 className="font-serif text-[2.6rem] leading-[1.08] tracking-tight sm:text-[3.4rem]">{L("L'intelligence bancaire qui montre ses preuves.", "Banking intelligence that shows its evidence.")}</h1>
          <p className="mt-6 max-w-[34rem] text-[1.06rem] leading-relaxed text-ink-2">
            {L("Fraude, LCB-FT, crédit, documents, prévisions, copilote et agents : une plateforme multi-banques où chaque score se rattache à un modèle versionné, chaque modèle à ses données et à sa revue, et chaque action automatisable à une approbation humaine.",
              "Fraud, AML, credit, documents, forecasting, copilot and agents: a multi-bank platform where every score traces back to a versioned model, every model to its data and review, and every automatable action to a human approval.")}
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/demo" className="rounded-[var(--radius-control)] bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink hover:bg-accent-hover">{L("Explorer la banque de démonstration", "Explore the demo bank")}</Link>
            <Link href="/solutions" className="rounded-[var(--radius-control)] border border-line-strong px-4 py-2.5 text-sm font-medium hover:bg-surface-2">{L("Voir les modules", "See the modules")}</Link>
          </div>
        </div>
        <figure className="rounded-[var(--radius-panel)] border border-line bg-surface">
          <figcaption className="flex items-baseline justify-between border-b border-line px-5 py-3">
            <span className="font-serif text-lg">{L("Registre des preuves", "Evidence register")}</span>
            <span className="text-xs text-ink-3">{L("modèles en production, mesures sur jeu de test", "production models, test-set measurements")}</span>
          </figcaption>
          {ev.isLoading ? <p className="px-5 py-6 text-sm text-ink-3">{L("Chargement…", "Loading…")}</p> : ev.error || !ev.data ? (
            <p className="px-5 py-6 text-sm text-ink-3">{L("Le registre n'est pas joignable pour le moment.", "The register cannot be reached right now.")}</p>
          ) : ev.data.models.length === 0 ? <p className="px-5 py-6 text-sm text-ink-3">{L("Aucun modèle en production.", "No model in production.")}</p> : (
            <table className="w-full text-sm">
              <tbody>{ev.data.models.map((m) => (
                <tr key={m.name} className="border-b border-line last:border-0 align-baseline">
                  <td className="px-5 py-2.5"><p className="font-medium">{modLabel(m.module)}</p><p className="text-xs text-ink-3">{m.name} v{m.version}</p></td>
                  <td className="px-3 py-2.5 text-xs text-ink-3">{m.headline?.label}</td>
                  <td className="px-5 py-2.5 text-right num text-lg font-semibold">{m.headline ? (typeof m.headline.value === "number" ? m.headline.value.toFixed(3) : m.headline.value) : "—"}</td>
                </tr>))}</tbody>
            </table>
          )}
          {ev.data && <p className="border-t border-line px-5 py-2.5 text-xs text-ink-3">{ev.data.notice}</p>}
        </figure>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-14 sm:px-6" aria-labelledby="modules">
        <h2 id="modules" className="font-serif text-2xl">{L("Dix modules, quatre métiers", "Ten modules, four jobs")}</h2>
        <div className="mt-8 grid gap-10 md:grid-cols-2">
          {GROUPS.map((g) => (
            <div key={g.fr}>
              <h3 className="border-b border-ink pb-1.5 text-sm font-semibold">{locale === "en" ? g.en : g.fr}</h3>
              <dl className="mt-3 flex flex-col gap-3">{g.items.map((it) => (
                <div key={it[0]}><dt className="font-medium">{locale === "en" ? it[1] : it[0]}</dt><dd className="text-sm text-ink-2">{locale === "en" ? it[3] : it[2]}</dd></div>))}</dl>
            </div>))}
        </div>
      </section>

      <section className="border-y border-line bg-surface" aria-labelledby="limits">
        <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 lg:grid-cols-[1fr_1.4fr]">
          <div>
            <h2 id="limits" className="font-serif text-2xl">{L("Ce que la plateforme ne fait pas", "What the platform does not do")}</h2>
            <p className="mt-3 text-ink-2">{L("Des limites écrites dans le code, pas seulement dans une charte.", "Limits written into the code, not only into a charter.")}</p>
          </div>
          <ul className="grid gap-x-8 gap-y-4 sm:grid-cols-2 text-[0.95rem]">
            {[
              [L("Aucune décision de crédit automatisée", "No automated credit decision"), L("Le score éclaire ; l'analyste décide et motive.", "The score informs; the analyst decides and explains.")],
              [L("Aucun virement par un agent", "No transfer by an agent"), L("Les agents lisent et proposent ; une personne habilitée approuve.", "Agents read and propose; an authorised person approves.")],
              [L("Une alerte n'est pas une preuve", "An alert is not proof"), L("La clôture LCB-FT exige un second regard.", "Closing an AML case requires a second reviewer.")],
              [L("Pas de vérification d'identité certifiée", "No certified identity verification"), L("Les contrôles documentaires assistent la revue humaine.", "Document controls assist human review.")],
              [L("Pas de prévision présentée comme certaine", "No forecast shown as certain"), L("Chaque projection porte ses intervalles.", "Every projection carries its intervals.")],
              [L("Pas de chiffre inventé", "No invented figure"), L("Une fonction indisponible est signalée comme telle.", "An unavailable function is reported as such.")],
            ].map(([a, b]) => <li key={a}><p className="font-medium">{a}</p><p className="text-sm text-ink-2">{b}</p></li>)}
          </ul>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 lg:grid-cols-2" aria-labelledby="trust">
        <div>
          <h2 id="trust" className="font-serif text-2xl">{L("Isolation et traçabilité", "Isolation and traceability")}</h2>
          <ul className="mt-4 flex flex-col gap-2 text-ink-2">
            <li>{L("Chaque banque est isolée par la sécurité au niveau des lignes de PostgreSQL, forcée et testée ; base dédiée possible.", "Each bank is isolated by PostgreSQL row-level security, forced and tested; dedicated database possible.")}</li>
            <li>{L("Neuf rôles, droits vérifiés côté serveur, double authentification obligatoire pour l'administration et la conformité.", "Nine roles, permissions checked server-side, two-factor authentication required for administration and compliance.")}</li>
            <li>{L("Journal d'audit en ajout seul, chaîné par hachage et vérifiable.", "Append-only, hash-chained, verifiable audit log.")}</li>
            <li>{L("Conçue pour AWS Paris (eu-west-3) : déploiement soumis à validation budgétaire.", "Designed for AWS Paris (eu-west-3): deployment subject to budget approval.")}</li>
          </ul>
        </div>
        <div>
          <h2 className="font-serif text-2xl">{L("Données d'entraînement", "Training data")}</h2>
          <ul className="mt-4 flex flex-col divide-y divide-line text-sm">
            {(ev.data?.sources ?? []).map((s) => <li key={s.key} className="flex justify-between gap-4 py-1.5"><span>{s.name}</span><span className="shrink-0 text-ink-3">{s.license}</span></li>)}
          </ul>
        </div>
      </section>
    </SiteShell>
  );
}
