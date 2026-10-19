"use client";

import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "quiet";
type Size = "sm" | "md";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-ink hover:bg-accent-hover border border-transparent",
  secondary: "bg-surface text-ink border border-line-strong hover:bg-surface-2",
  ghost: "bg-transparent text-ink-2 hover:text-ink hover:bg-surface-2 border border-transparent",
  danger: "bg-risk-critical text-white hover:opacity-90 border border-transparent",
  quiet: "bg-surface-2 text-ink hover:bg-surface-3 border border-transparent",
};

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: Size; loading?: boolean; icon?: ReactNode;
}>(function Button({ variant = "secondary", size = "md", loading, icon, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-[var(--radius-control)] font-medium transition-colors",
        "disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap",
        size === "sm" ? "h-8 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        variants[variant],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

const field = "w-full rounded-[var(--radius-control)] border border-line-strong bg-surface px-3 text-sm text-ink placeholder:text-ink-3 disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cn(field, "h-9", className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cn(field, "py-2 min-h-24 leading-relaxed", className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...rest }, ref) {
  return (
    <select ref={ref} className={cn(field, "h-9 pr-8 appearance-none bg-[length:12px] bg-[right_10px_center] bg-no-repeat", className)}
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M2 4l4 4 4-4' fill='none' stroke='%236b7486' stroke-width='1.5'/%3E%3C/svg%3E\")" }}
      {...rest}>
      {children}
    </select>
  );
});

export function Field({ label, hint, error, children, htmlFor, className }: {
  label: string; hint?: string; error?: string; children: ReactNode; htmlFor?: string; className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink-2">{label}</label>
      {children}
      {hint && !error && <p className="text-xs text-ink-3">{hint}</p>}
      {error && <p className="text-xs text-risk-critical" role="alert">{error}</p>}
    </div>
  );
}

export function Badge({ children, tone = "neutral", className }: {
  children: ReactNode; tone?: "neutral" | "accent" | "info" | "ok" | "medium" | "high" | "critical" | "demo"; className?: string;
}) {
  const tones: Record<string, string> = {
    neutral: "bg-surface-2 text-ink-2 border-line",
    accent: "bg-accent-soft text-accent border-transparent",
    info: "bg-surface-2 text-info border-line",
    ok: "bg-surface-2 text-ok border-line",
    medium: "bg-risk-medium-soft text-risk-medium border-transparent",
    high: "bg-risk-high-soft text-risk-high border-transparent",
    critical: "bg-risk-critical-soft text-risk-critical border-transparent",
    demo: "bg-risk-medium-soft text-demo border-transparent font-semibold",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap", tones[tone], className)}>
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-3">{children}</kbd>;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-ink-3", className)} aria-hidden />;
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn("relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors disabled:opacity-50",
        checked ? "bg-accent border-accent" : "bg-surface-3 border-line-strong")}>
      <span className={cn("inline-block size-3.5 rounded-full bg-white shadow transition-transform", checked ? "translate-x-4.5" : "translate-x-0.5")} />
    </button>
  );
}
