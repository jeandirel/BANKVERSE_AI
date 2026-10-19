"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { messages, type MessageKey } from "./messages";

export type Locale = "fr" | "en";

type Ctx = { locale: Locale; setLocale: (l: Locale) => void; t: (k: MessageKey, vars?: Record<string, string | number>) => string };

const I18nContext = createContext<Ctx | null>(null);

function initialLocale(): Locale {
  if (typeof document === "undefined") return "fr";
  const m = document.cookie.match(/(?:^|; )bv_locale=(fr|en)/);
  if (m) return m[1] as Locale;
  return navigator.language?.toLowerCase().startsWith("en") ? "en" : "fr";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("fr");
  useEffect(() => setLocaleState(initialLocale()), []);
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  const setLocale = useCallback((l: Locale) => {
    document.cookie = `bv_locale=${l}; path=/; max-age=31536000; samesite=lax`;
    setLocaleState(l);
  }, []);
  const t = useCallback(
    (k: MessageKey, vars?: Record<string, string | number>) => {
      let s: string = messages[locale][k] ?? messages.fr[k] ?? k;
      if (vars) for (const [name, v] of Object.entries(vars)) s = s.replaceAll(`{${name}}`, String(v));
      return s;
    },
    [locale],
  );
  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Ctx {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}

/** Pick the localized variant of a {fr, en} label object returned by the API. */
export function pick(label: { fr?: string; en?: string } | undefined, locale: Locale, fallback = ""): string {
  if (!label) return fallback;
  return label[locale] ?? label.fr ?? label.en ?? fallback;
}

/** Inline bilingual copy for page-specific text: L("Texte", "Text"). */
export function useL(): (fr: string, en: string) => string {
  const { locale } = useI18n();
  return useCallback((fr: string, en: string) => (locale === "fr" ? fr : en), [locale]);
}
