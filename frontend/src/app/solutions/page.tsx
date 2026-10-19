"use client";

import Link from "next/link";
import { SiteShell } from "@/components/site/site-chrome";
import { useI18n, useL } from "@/lib/i18n";

type Mod = { id: string; fr: string; en: string; what: [string, string]; data: [string, string]; models: [string, string]; human: [string, string]; limits: [string, string] };

const MODULES: Mod[] = [
  { id: "data", fr: "Plateforme de données", en: "Data platform",
    what: ["Ingestion vérifiée (empreintes SHA-256), couches bronze / silver / gold en Parquet, contrôles qualité bloquants, lignage jusqu'aux modèles, imports des banques profilés.", "Verified ingestion (SHA-256 fingerprints), bronze / silver / gold Parquet layers, blocking quality checks, lineage up to models, profiled bank uploads."],
    data: ["IBM TabFormer, IBM AMLSim, UCI Credit Default, UCI Bank Marketing, BANKING77, FinQA, séries macro DataHub.", "IBM TabFormer, IBM AMLSim, UCI Credit Default, UCI Bank Marketing, BANKING77, FinQA, DataHub macro series."],
    models: ["Polars pour les transformations, PySpark pour le contrôle de parité, DuckDB.", "Polars for transformations, PySpark for the parity check, DuckDB."],
    human: ["Les sources aux droits incertains ne sont pas importées automatiquement.", "Sources with uncertain rights are not imported automatically."],
    limits: ["Les connecteurs BCE, FRED, SEC, Kaggle sont décrits mais nécessitent des accès non disponibles dans cet environnement.", "ECB, FRED, SEC, Kaggle connectors are described but need access not available in this environment."] },
  { id: "fraud", fr: "Détection de fraude carte", en: "Card fraud detection",
    what: ["Score de chaque paiement avec l'historique de la carte sur 31 jours, raisons du modèle, alertes avec délai de traitement, simulateur.", "Every payment scored with 31 days of card history, model reasons, alerts with handling deadlines, simulator."],
    data: ["24,4 millions de transactions TabFormer, découpage temporel train / validation / test.", "24.4 million TabFormer transactions, temporal train / validation / test split."],
    models: ["Régression logistique, forêt aléatoire, XGBoost, LightGBM, Isolation Forest, autoencodeur, LSTM et Transformer sur séquences ; seuil optimisé au coût.", "Logistic regression, random forest, XGBoost, LightGBM, Isolation Forest, autoencoder, LSTM and Transformer on sequences; cost-optimal threshold."],
    human: ["La qualification et tout blocage de carte restent humains.", "Qualification and any card block remain human."],
    limits: ["Données publiques synthétiques d'IBM : les performances sur un portefeuille réel doivent être mesurées en pilote.", "IBM public synthetic data: performance on a real portfolio must be measured in a pilot."] },
  { id: "aml", fr: "LCB-FT", en: "AML",
    what: ["Règles de typologies (fractionnement, cycles, comptes de passage, dispersion), apprentissage sur graphe, réseau de contreparties, dossiers à quatre yeux.", "Typology rules (structuring, cycles, pass-through, fan-out), graph learning, counterparty network, four-eyes cases."],
    data: ["IBM AMLSim (graphes de transactions avec typologies étiquetées), évaluation inductive sur un graphe non vu.", "IBM AMLSim (transaction graphs with labelled typologies), inductive evaluation on an unseen graph."],
    models: ["Règles, Isolation Forest, régression logistique, LightGBM, GraphSAGE (PyTorch Geometric).", "Rules, Isolation Forest, logistic regression, LightGBM, GraphSAGE (PyTorch Geometric)."],
    human: ["Une alerte n'est pas une preuve ; la déclaration relève du seul responsable conformité.", "An alert is not proof; reporting is solely the compliance officer's decision."],
    limits: ["Simulation publique : pas de calibrage sur des flux bancaires réels.", "Public simulation: no calibration on real bank flows."] },
  { id: "credit", fr: "Risque de crédit", en: "Credit risk",
    what: ["Probabilité de défaut calibrée, grades A–E, facteurs explicatifs, simulations, rapport d'équité.", "Calibrated probability of default, A–E grades, explanatory factors, what-ifs, fairness report."],
    data: ["UCI Default of Credit Card Clients (30 000 clients).", "UCI Default of Credit Card Clients (30,000 customers)."],
    models: ["Régression logistique, forêt aléatoire, XGBoost, LightGBM, MLP ; calibration isotonique ou de Platt choisie en validation.", "Logistic regression, random forest, XGBoost, LightGBM, MLP; isotonic or Platt calibration chosen on validation."],
    human: ["Aucune décision d'octroi ou de refus automatisée.", "No automated approval or refusal."],
    limits: ["Population taïwanaise de 2005 : non transposable sans recalibrage.", "Taiwanese 2005 population: not transferable without recalibration."] },
  { id: "forecasting", fr: "Prévisions", en: "Forecasting",
    what: ["Huit modèles comparés en backtest glissant, diagnostics, corrélations croisées, scénarios conditionnels (SARIMAX).", "Eight models compared by rolling backtest, diagnostics, cross-correlations, conditional scenarios (SARIMAX)."],
    data: ["Huit séries macro-financières mensuelles publiques (taux, change, inflation, matières premières, volatilité).", "Eight public monthly macro-financial series (rates, FX, inflation, commodities, volatility)."],
    models: ["Naïf, saisonnier naïf, moyenne mobile, ETS, ARIMA, SARIMA, XGBoost sur retards, LSTM.", "Naive, seasonal naive, moving average, ETS, ARIMA, SARIMA, XGBoost on lags, LSTM."],
    human: ["Les projections portent leurs intervalles et ne sont pas des conseils.", "Projections carry their intervals and are not advice."],
    limits: ["Pas de données de trésorerie propres à une banque dans la démonstration.", "No bank-specific treasury data in the demo."] },
  { id: "documents", fr: "Documents & KYC", en: "Documents & KYC",
    what: ["Validation sécurisée des fichiers, OCR, classification, extraction avec positions, contrôles (IBAN, SIREN, soldes, totaux, fraîcheur), revue à quatre yeux.", "Secure file validation, OCR, classification, positioned extraction, controls (IBAN, SIREN, balances, totals, freshness), four-eyes review."],
    data: ["Spécimens DEMO générés (5 types, 3 mises en page, versions scannées) ; aucun vrai document.", "Generated DEMO specimens (5 types, 3 layouts, scanned versions); no real document."],
    models: ["TF-IDF et régression logistique testés sur une mise en page jamais vue ; Tesseract pour l'OCR.", "TF-IDF and logistic regression tested on an unseen layout; Tesseract for OCR."],
    human: ["Ce n'est pas une vérification d'identité certifiée.", "This is not a certified identity verification."],
    limits: ["Corpus synthétique régulier : les scores sont une borne haute.", "Regular synthetic corpus: scores are an upper bound."] },
  { id: "customer", fr: "Connaissance client", en: "Customer intelligence",
    what: ["Thèmes des messages et réclamations, recherche sémantique, segmentation comportementale, appétence campagne.", "Message topics and complaints, semantic search, behavioural segmentation, campaign propensity."],
    data: ["BANKING77 (13 000 requêtes), UCI Bank Marketing (41 000 contacts).", "BANKING77 (13,000 queries), UCI Bank Marketing (41,000 contacts)."],
    models: ["TF-IDF + régression logistique, embeddings LSA, K-Means et DBSCAN, LightGBM.", "TF-IDF + logistic regression, LSA embeddings, K-Means and DBSCAN, LightGBM."],
    human: ["Les réclamations ouvertes passent avant toute sollicitation commerciale.", "Open complaints come before any commercial contact."],
    limits: ["Classification en anglais uniquement.", "English-only classification."] },
  { id: "copilot", fr: "Copilote financier", en: "Financial copilot",
    what: ["Recherche hybride (plein texte + vecteurs) avec reranker appris, citations vérifiables, calculatrice sûre, base documentaire privée par banque.", "Hybrid search (full text + vectors) with a learned reranker, verifiable citations, safe calculator, private knowledge base per bank."],
    data: ["FinQA : 2 789 pages de rapports 10-K et 8 281 questions d'experts.", "FinQA: 2,789 10-K report pages and 8,281 expert questions."],
    models: ["BM25, embeddings LSA, LightGBM LambdaRank ; synthèse par LLM uniquement si un fournisseur est configuré.", "BM25, LSA embeddings, LightGBM LambdaRank; LLM synthesis only when a provider is configured."],
    human: ["Refus du conseil en investissement et des demandes d'action ; blocage des injections de consignes.", "Refuses investment advice and action requests; blocks prompt injection."],
    limits: ["Sans fournisseur LLM, le copilote est extractif.", "Without an LLM provider the copilot is extractive."] },
  { id: "agents", fr: "Agents IA", en: "AI agents",
    what: ["Huit agents orchestrés par LangGraph, outils typés, budgets d'étapes et de temps, traces complètes, approbations humaines.", "Eight agents orchestrated by LangGraph, typed tools, step and time budgets, full traces, human approvals."],
    data: ["Les données de la banque, selon les droits de l'utilisateur.", "The bank's data, within the user's permissions."],
    models: ["Planification déterministe et auditable ; rédaction par LLM en option.", "Deterministic, auditable planning; optional LLM drafting."],
    human: ["Toute action (ouvrir un dossier, escalader, générer un rapport) attend une approbation et s'exécute avec les droits de l'approbateur.", "Every action (open a case, escalate, generate a report) awaits approval and runs with the approver's permissions."],
    limits: ["Aucun agent n'effectue de virement ni de décision réglementaire.", "No agent makes transfers or regulatory decisions."] },
  { id: "governance", fr: "Gouvernance & MLOps", en: "Governance & MLOps",
    what: ["Registre versionné, fiches modèles, revue à quatre yeux, déploiement et retour arrière, dérive (PSI), incidents, rapport PDF.", "Versioned registry, model cards, four-eyes review, deployment and rollback, drift (PSI), incidents, PDF report."],
    data: ["Métadonnées d'entraînement, profils de référence, journaux d'inférence par banque.", "Training metadata, reference profiles, per-bank inference logs."],
    models: ["MLflow pour le suivi des expériences ; artefacts vérifiés par empreinte avant chargement.", "MLflow experiment tracking; artifacts verified by fingerprint before loading."],
    human: ["La personne qui entraîne un modèle ne peut pas l'approuver.", "The person who trains a model cannot approve it."],
    limits: ["Validation indépendante et revue juridique non réalisées.", "Independent validation and legal review not performed."] },
];

export default function SolutionsPage() {
  const { locale } = useI18n();
  const L = useL();
  const p = (x: [string, string]) => (locale === "en" ? x[1] : x[0]);
  return (
    <SiteShell>
      <div className="mx-auto grid max-w-6xl gap-10 px-4 pt-14 sm:px-6 lg:grid-cols-[200px_1fr]">
        <nav aria-label={L("Modules", "Modules")} className="lg:sticky lg:top-6 lg:self-start">
          <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm lg:flex-col">{MODULES.map((m) => <li key={m.id}><a href={`#${m.id}`} className="text-ink-2 hover:text-ink">{locale === "en" ? m.en : m.fr}</a></li>)}</ul>
        </nav>
        <div>
          <h1 className="font-serif text-4xl tracking-tight">{L("Les modules", "The modules")}</h1>
          <p className="mt-3 max-w-2xl text-ink-2">{L("Pour chacun : ce qu'il fait, sur quelles données il a été entraîné, quels modèles il compare, où s'arrête l'automatisation, et ses limites connues.", "For each: what it does, the data it was trained on, the models it compares, where automation stops, and its known limits.")}</p>
          {MODULES.map((m) => (
            <section key={m.id} id={m.id} className="mt-12 scroll-mt-6 border-t border-line pt-6">
              <h2 className="font-serif text-2xl">{locale === "en" ? m.en : m.fr}</h2>
              <p className="mt-2 max-w-3xl text-ink-2 leading-relaxed">{p(m.what)}</p>
              <dl className="mt-4 grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
                <div><dt className="font-medium">{L("Données", "Data")}</dt><dd className="text-ink-2">{p(m.data)}</dd></div>
                <div><dt className="font-medium">{L("Modèles", "Models")}</dt><dd className="text-ink-2">{p(m.models)}</dd></div>
                <div><dt className="font-medium">{L("Rôle humain", "Human role")}</dt><dd className="text-ink-2">{p(m.human)}</dd></div>
                <div><dt className="font-medium">{L("Limites", "Limits")}</dt><dd className="text-ink-2">{p(m.limits)}</dd></div>
              </dl>
            </section>))}
          <p className="mt-14"><Link href="/demo" className="rounded-[var(--radius-control)] bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink hover:bg-accent-hover">{L("Essayer dans la banque de démonstration", "Try it in the demo bank")}</Link></p>
        </div>
      </div>
    </SiteShell>
  );
}
