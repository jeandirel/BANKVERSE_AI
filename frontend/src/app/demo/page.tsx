"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { SiteShell } from "@/components/site/site-chrome";
import { ApiError, post } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";

const ROLES: { role: string; fr: string; en: string; dfr: string; den: string; start: string }[] = [
  { role: "fraud_analyst", fr: "Analyste fraude", en: "Fraud analyst", dfr: "Trier la file d'alertes, examiner les raisons du modèle, simuler des transactions, ouvrir un dossier.", den: "Triage the alert queue, review model reasons, simulate transactions, open a case.", start: "/app/fraud" },
  { role: "aml_analyst", fr: "Analyste LCB-FT", en: "AML analyst", dfr: "Instruire une alerte, explorer le réseau de contreparties, documenter le dossier.", den: "Investigate an alert, explore the counterparty network, document the case.", start: "/app/aml" },
  { role: "credit_analyst", fr: "Analyste crédit", en: "Credit analyst", dfr: "Étudier une demande, simuler des variations, revoir les justificatifs, décider.", den: "Study an application, simulate changes, review documents, decide.", start: "/app/credit" },
  { role: "risk_manager", fr: "Responsable des risques", en: "Risk manager", dfr: "Vue d'ensemble, prévisions et scénarios, approbation des actions d'agents.", den: "Overview, forecasts and scenarios, approval of agent actions.", start: "/app" },
  { role: "data_scientist", fr: "Data scientist", en: "Data scientist", dfr: "Catalogue, qualité et lignage des données ; preuves d'évaluation des modèles.", den: "Data catalogue, quality and lineage; model evaluation evidence.", start: "/app/data" },
  { role: "auditor", fr: "Auditeur", en: "Auditor", dfr: "Lecture seule : journal d'audit chaîné, dossiers, gouvernance.", den: "Read-only: hash-chained audit log, cases, governance.", start: "/app/admin" },
];

export default function DemoPage() {
  const { locale } = useI18n();
  const L = useL();
  const router = useRouter();
  const qc = useQueryClient();
  const go = useMutation({
    mutationFn: (r: (typeof ROLES)[number]) => post("/auth/demo-session", { role: r.role }).then(() => r),
    onSuccess: (r) => { qc.clear(); router.push(r.start); },
  });
  return (
    <SiteShell>
      <section className="mx-auto max-w-5xl px-4 pt-16 sm:px-6">
        <h1 className="font-serif text-4xl tracking-tight">{L("Banque de démonstration", "Demo bank")}</h1>
        <p className="mt-4 max-w-2xl text-ink-2 leading-relaxed">{L("Une banque fictive : 1 500 clients, 225 000 paiements carte, 12 000 virements, des scénarios de fraude et de blanchiment injectés. Les modèles sont ceux entraînés sur des données publiques ; toutes les données de cette banque sont marquées DEMO.",
          "A fictitious bank: 1,500 customers, 225,000 card payments, 12,000 transfers, injected fraud and money-laundering scenarios. The models are those trained on public data; every record of this bank is marked DEMO.")}</p>
        <p className="mt-3 max-w-2xl text-sm text-ink-3">{L("Choisissez un rôle : vous verrez exactement ce que ce rôle est autorisé à voir et à faire. Les rôles d'administration et de conformité exigent une double authentification et ne sont pas proposés ici.",
          "Pick a role: you will see exactly what that role is allowed to see and do. Administration and compliance roles require two-factor authentication and are not offered here.")}</p>
        {go.error && <p role="alert" className="mt-4 text-sm text-risk-high">{go.error instanceof ApiError ? go.error.message : String(go.error)}</p>}
        <ul className="mt-10 grid gap-px overflow-hidden rounded-[var(--radius-panel)] border border-line bg-line sm:grid-cols-2">
          {ROLES.map((r) => (
            <li key={r.role} className="bg-surface">
              <button type="button" disabled={go.isPending} onClick={() => go.mutate(r)} className="flex h-full w-full flex-col items-start gap-1.5 px-5 py-4 text-left hover:bg-surface-2 disabled:opacity-60">
                <span className="font-medium">{locale === "en" ? r.en : r.fr}{go.isPending && go.variables?.role === r.role ? " …" : ""}</span>
                <span className="text-sm text-ink-2">{locale === "en" ? r.den : r.dfr}</span>
              </button>
            </li>))}
        </ul>
      </section>
    </SiteShell>
  );
}
