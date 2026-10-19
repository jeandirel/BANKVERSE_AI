import type { Locale } from "./i18n";

const tag = (l: Locale) => (l === "fr" ? "fr-FR" : "en-GB");

export function fmtNumber(v: number | null | undefined, l: Locale, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return new Intl.NumberFormat(tag(l), { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v);
}

export function fmtCompact(v: number | null | undefined, l: Locale): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat(tag(l), { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

export function fmtMoney(v: number | null | undefined, l: Locale, currency = "EUR", digits = 2): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat(tag(l), { style: "currency", currency, maximumFractionDigits: digits }).format(v);
}

export function fmtPct(v: number | null | undefined, l: Locale, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat(tag(l), { style: "percent", maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v);
}

export function fmtScore(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(3);
}

export function fmtDate(v: string | Date | null | undefined, l: Locale, withTime = false): string {
  if (!v) return "—";
  const d = typeof v === "string" ? new Date(v) : v;
  return new Intl.DateTimeFormat(tag(l), {
    year: "numeric",
    month: "short",
    day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(d);
}

export function fmtRelative(v: string | Date | null | undefined, l: Locale): string {
  if (!v) return "—";
  const d = typeof v === "string" ? new Date(v) : v;
  const diff = (d.getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(tag(l), { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}
