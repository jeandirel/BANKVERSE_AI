"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Dialog({ open, onOpenChange, title, description, children, footer, wide }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; description?: string; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[rgb(13_20_34/0.45)]" />
        <DialogPrimitive.Content className={cn("fixed left-1/2 top-[10vh] z-50 -translate-x-1/2 w-[calc(100vw-32px)] rounded-[var(--radius-panel)] border border-line bg-surface shadow-[var(--shadow-pop)] max-h-[80vh] flex flex-col", wide ? "max-w-3xl" : "max-w-lg")}>
          <div className="flex items-start justify-between gap-4 px-5 pt-4 pb-3 border-b border-line">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold text-ink">{title}</DialogPrimitive.Title>
              {description && <DialogPrimitive.Description className="mt-1 text-sm text-ink-3">{description}</DialogPrimitive.Description>}
            </div>
            <DialogPrimitive.Close className="rounded p-1 text-ink-3 hover:text-ink hover:bg-surface-2" aria-label="Close">
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="px-5 py-4 overflow-y-auto scroll-thin">{children}</div>
          {footer && <div className="flex justify-end gap-2 px-5 py-3 border-t border-line">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function Tabs({ value, onValueChange, items, children }: {
  value: string; onValueChange: (v: string) => void; items: { value: string; label: string; count?: number }[]; children?: ReactNode;
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onValueChange}>
      <TabsPrimitive.List className="flex gap-1 border-b border-line overflow-x-auto scroll-thin" aria-label="Sections">
        {items.map((it) => (
          <TabsPrimitive.Trigger key={it.value} value={it.value}
            className="relative px-3 py-2 text-sm text-ink-3 hover:text-ink whitespace-nowrap data-[state=active]:text-ink data-[state=active]:font-medium data-[state=active]:after:absolute data-[state=active]:after:inset-x-2 data-[state=active]:after:-bottom-px data-[state=active]:after:h-0.5 data-[state=active]:after:bg-accent">
            {it.label}
            {it.count !== undefined && <span className="num ml-1.5 text-xs text-ink-3">{it.count}</span>}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {children}
    </TabsPrimitive.Root>
  );
}

export const TabPanel = TabsPrimitive.Content;

export function Tip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={250}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content sideOffset={6} className="z-50 max-w-xs rounded border border-line bg-surface px-2.5 py-1.5 text-xs text-ink shadow-[var(--shadow-pop)]">
            {content}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

type Toast = { id: number; text: string; tone: "ok" | "error" | "info" };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, tone }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={cn("max-w-sm rounded-[var(--radius-panel)] border bg-surface px-3.5 py-2.5 text-sm shadow-[var(--shadow-pop)]",
            t.tone === "error" ? "border-risk-critical/40 text-risk-critical" : "border-line text-ink")}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);
