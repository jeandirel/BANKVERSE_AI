"use client";

import type { ReactNode } from "react";
import { AlertTriangle, Info, Lock, RefreshCcw } from "lucide-react";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useI18n } from "@/lib/i18n";
import { Button, Spinner } from "./primitives";

export function PageHeader({ title, subtitle, actions, meta }: { title: string; subtitle?: string; actions?: ReactNode; meta?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 pb-5 border-b border-line mb-6">
      <div className="min-w-0 max-w-3xl">
        <h1 className="page-title text-[26px] leading-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1.5 text-sm text-ink-2 leading-relaxed">{subtitle}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A titled region. Panels have a hairline border; use `flush` for tables that own their padding. */
export function Panel({ title, description, actions, children, className, flush, id }: {
  title?: string; description?: string; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean; id?: string;
}) {
  return (
    <section id={id} className={cn("rounded-[var(--radius-panel)] border border-line bg-surface", className)} aria-label={title}>
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-3.5 pb-3 border-b border-line">
          <div className="min-w-0">
            {title && <h2 className="text-[15px] font-semibold text-ink">{title}</h2>}
            {description && <p className="mt-0.5 text-[13px] text-ink-3 leading-snug">{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

/** Ledger strip: key figures separated by rules, read left to right like a statement header. */
export function Ledger({ items }: { items: { label: string; value: ReactNode; hint?: string; tone?: "critical" | "high" | "medium" | "ok" }[] }) {
  return (
    <dl className="grid grid-cols-2 md:grid-cols-[repeat(auto-fit,minmax(150px,1fr))] rounded-[var(--radius-panel)] border border-line bg-surface">
      {items.map((it, i) => (
        <div key={it.label} className={cn("px-4 py-3.5 border-line", i > 0 && "md:border-l", i % 2 === 1 && "border-l md:border-l", i >= 2 && "border-t md:border-t-0")}>
          <dt className="text-[13px] text-ink-3">{it.label}</dt>
          <dd className={cn("num mt-1 text-[22px] font-semibold leading-none",
            it.tone === "critical" ? "text-risk-critical" : it.tone === "high" ? "text-risk-high" : it.tone === "medium" ? "text-risk-medium" : it.tone === "ok" ? "text-ok" : "text-ink")}>
            {it.value}
          </dd>
          {it.hint && <p className="mt-1.5 text-xs text-ink-3">{it.hint}</p>}
        </div>
      ))}
    </dl>
  );
}

export function LoadingBlock({ label, className }: { label?: string; className?: string }) {
  const { t } = useI18n();
  return (
    <div className={cn("flex items-center gap-2 py-10 justify-center text-sm text-ink-3", className)} role="status">
      <Spinner /> {label ?? t("common.loading")}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-surface-2", className)} aria-hidden />;
}

export function EmptyState({ title, body, action, icon }: { title: string; body?: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center text-center gap-2 py-12 px-6">
      {icon && <div className="text-ink-3 mb-1">{icon}</div>}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {body && <p className="text-sm text-ink-3 max-w-md leading-relaxed">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n();
  const e = error instanceof ApiError ? error : null;
  if (e?.status === 403) {
    return <EmptyState icon={<Lock className="size-5" />} title={t("common.forbidden")} body={e.message} />;
  }
  const unavailable = e?.status === 503;
  return (
    <div className="flex flex-col items-center text-center gap-2 py-10 px-6" role="alert">
      <AlertTriangle className={cn("size-5", unavailable ? "text-risk-medium" : "text-risk-high")} />
      <p className="text-sm font-semibold text-ink">{unavailable ? t("common.unavailable") : t("common.error")}</p>
      <p className="text-sm text-ink-2 max-w-lg">{e?.message ?? (error instanceof Error ? error.message : String(error))}</p>
      {e?.requestId && <p className="text-xs text-ink-3">{t("common.requestId")} : <span className="num">{e.requestId}</span></p>}
      {onRetry && !unavailable && <Button size="sm" onClick={onRetry} icon={<RefreshCcw className="size-3.5" />}>{t("common.retry")}</Button>}
    </div>
  );
}

export function Notice({ children, tone = "info", title }: { children: ReactNode; tone?: "info" | "warn" | "demo"; title?: string }) {
  return (
    <div className={cn("flex gap-3 rounded-[var(--radius-panel)] border px-3.5 py-3 text-[13px] leading-relaxed",
      tone === "warn" ? "border-risk-high/30 bg-risk-high-soft text-ink" : tone === "demo" ? "border-demo/40 bg-risk-medium-soft text-ink" : "border-line bg-surface-2 text-ink-2")}>
      <Info className={cn("size-4 shrink-0 mt-0.5", tone === "warn" ? "text-risk-high" : tone === "demo" ? "text-demo" : "text-info")} aria-hidden />
      <div>
        {title && <p className="font-semibold text-ink mb-0.5">{title}</p>}
        {children}
      </div>
    </div>
  );
}

export function KeyValue({ rows, className }: { rows: [string, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(110px,40%)_1fr] gap-x-4 gap-y-2 text-sm", className)}>
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd className="text-ink min-w-0 break-words">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}
