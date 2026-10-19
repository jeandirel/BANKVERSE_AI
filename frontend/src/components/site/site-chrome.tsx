"use client";

import { Moon, Sun } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useI18n, useL } from "@/lib/i18n";

function ThemeToggle() {
  const L = useL();
  const [dark, setDark] = useState(false);
  useEffect(() => setDark(document.documentElement.getAttribute("data-theme") === "dark"), []);
  const flip = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("bv_theme", next); } catch { /* storage unavailable */ }
    setDark(!dark);
  };
  return <button type="button" onClick={flip} aria-label={dark ? L("Thème clair", "Light theme") : L("Thème sombre", "Dark theme")} className="rounded p-1.5 text-ink-2 hover:text-ink hover:bg-surface-2">{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</button>;
}

export function SiteShell({ children }: { children: ReactNode }) {
  const { locale, setLocale } = useI18n();
  const L = useL();
  return (
    <div className="min-h-screen bg-bg text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-surface focus:px-3 focus:py-2 rounded">{L("Aller au contenu", "Skip to content")}</a>
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 sm:px-6">
          <Link href="/" className="font-serif text-lg font-semibold tracking-tight">BANKVERSE <span className="text-accent">AI</span></Link>
          <nav aria-label={L("Navigation principale", "Main navigation")} className="hidden items-center gap-5 text-sm text-ink-2 sm:flex">
            <Link href="/solutions" className="hover:text-ink">{L("Modules", "Modules")}</Link>
            <Link href="/demo" className="hover:text-ink">{L("Banque de démonstration", "Demo bank")}</Link>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button type="button" onClick={() => setLocale(locale === "fr" ? "en" : "fr")} className="rounded px-2 py-1 text-sm text-ink-2 hover:text-ink hover:bg-surface-2" aria-label={L("Passer en anglais", "Switch to French")}>{locale === "fr" ? "EN" : "FR"}</button>
            <ThemeToggle />
            <Link href="/login" className="rounded-[var(--radius-control)] border border-line-strong px-3 py-1.5 text-sm hover:bg-surface-2">{L("Se connecter", "Sign in")}</Link>
          </div>
        </div>
      </header>
      <main id="main">{children}</main>
      <footer className="mt-24 border-t border-line">
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-10 text-sm text-ink-3 sm:grid-cols-3 sm:px-6">
          <p className="font-serif text-ink">BANKVERSE AI</p>
          <p>{L("Prototype de plateforme SaaS B2B. Aucune certification ni conformité réglementaire n'est revendiquée ; les données de la banque de démonstration sont fictives.", "B2B SaaS platform prototype. No certification or regulatory compliance is claimed; the demo bank's data is fictitious.")}</p>
          <p>{L("Données publiques utilisées sous leurs licences d'origine (détail dans l'application, page Plateforme de données).", "Public data used under their original licences (details in the app, Data platform page).")}</p>
        </div>
      </footer>
    </div>
  );
}
