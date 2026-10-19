"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { get, qs } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useI18n, useL } from "@/lib/i18n";
import type { Me } from "@/lib/session";
import { Spinner } from "../ui/primitives";
import { visibleNav } from "./app-shell";

type Hit = { type: string; id: string; title: string; subtitle?: string; href: string };

export function CommandPalette({ open, onOpenChange, me }: { open: boolean; onOpenChange: (v: boolean) => void; me: Me }) {
  const { t } = useI18n();
  const L = useL();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(q.trim()), 220);
    return () => clearTimeout(id);
  }, [q]);
  useEffect(() => {
    if (!open) setQ("");
  }, [open]);
  const { data, isFetching } = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => get<{ hits: Hit[] }>(`/search${qs({ q: debounced })}`),
    enabled: open && debounced.length >= 2,
  });
  const navHits: Hit[] = useMemo(() => {
    const s = q.trim().toLowerCase();
    return visibleNav(me).flatMap((g) => g.items)
      .map((it) => ({ type: "page", id: it.href, title: t(it.label), href: it.href }))
      .filter((h) => !s || h.title.toLowerCase().includes(s));
  }, [me, q, t]);
  const hits = [...(data?.hits ?? []), ...navHits].slice(0, 14);
  useEffect(() => setActive(0), [debounced, q]);
  const go = (h: Hit) => {
    onOpenChange(false);
    router.push(h.href);
  };
  const typeLabel: Record<string, string> = {
    page: L("Page", "Page"), customer: L("Client", "Customer"), case: L("Dossier", "Case"),
    fraud_alert: L("Alerte fraude", "Fraud alert"), aml_alert: L("Alerte LCB-FT", "AML alert"),
    document: L("Document", "Document"), application: L("Demande de crédit", "Credit application"),
  };
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[rgb(13_20_34/0.45)]" />
        <DialogPrimitive.Content className="fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-32px)] max-w-xl -translate-x-1/2 rounded-[var(--radius-panel)] border border-line bg-surface shadow-[var(--shadow-pop)]">
          <DialogPrimitive.Title className="sr-only">{t("shell.search.short")}</DialogPrimitive.Title>
          <div className="flex items-center gap-2 border-b border-line px-3">
            <Search className="size-4 text-ink-3" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("shell.search")}
              className="h-12 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-3" aria-label={t("shell.search")}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
                if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
                if (e.key === "Enter" && hits[active]) go(hits[active]);
              }} />
            {isFetching && <Spinner />}
          </div>
          <ul className="max-h-[50vh] overflow-y-auto scroll-thin p-1" role="listbox">
            {hits.map((h, i) => (
              <li key={`${h.type}-${h.id}`} role="option" aria-selected={i === active}>
                <button onMouseEnter={() => setActive(i)} onClick={() => go(h)}
                  className={cn("flex w-full items-center gap-3 rounded px-2.5 py-2 text-left", i === active && "bg-surface-2")}>
                  <span className="w-28 shrink-0 text-xs text-ink-3">{typeLabel[h.type] ?? h.type}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink">{h.title}</span>
                    {h.subtitle && <span className="block truncate text-xs text-ink-3">{h.subtitle}</span>}
                  </span>
                  {i === active && <CornerDownLeft className="size-3.5 text-ink-3" />}
                </button>
              </li>
            ))}
            {hits.length === 0 && <li className="px-3 py-6 text-center text-sm text-ink-3">{L("Aucun résultat", "No results")}</li>}
          </ul>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
