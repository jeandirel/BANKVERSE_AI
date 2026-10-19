"use client";

import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity, Bot, Briefcase, Building2, ChartLine, ChevronsUpDown, Database, FileSearch, FileText, Gauge, Landmark,
  LogOut, Menu, MessagesSquare, Moon, Network, Scale, Search, Settings, ShieldAlert, ShieldCheck, Sun, Users, UserCog,
  Boxes, ServerCog, Building,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ComponentType, type ReactNode } from "react";
import { post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { pick, useI18n } from "@/lib/i18n";
import type { MessageKey } from "@/lib/messages";
import { useLogout, useMe, type Me } from "@/lib/session";
import { ErrorState, LoadingBlock } from "../ui/layout";
import { Kbd } from "../ui/primitives";
import { CommandPalette } from "./command-palette";

type NavItem = { href: string; label: MessageKey; icon: ComponentType<{ className?: string }>; perm?: string; module?: string; platform?: boolean };
type NavGroup = { label: MessageKey; items: NavItem[] };

export const NAV: NavGroup[] = [
  { label: "nav.group.overview", items: [{ href: "/app", label: "nav.dashboard", icon: Gauge, perm: "dashboard.read" }] },
  {
    label: "nav.group.detect",
    items: [
      { href: "/app/fraud", label: "nav.fraud", icon: ShieldAlert, perm: "fraud.read", module: "fraud" },
      { href: "/app/aml", label: "nav.aml", icon: Network, perm: "aml.read", module: "aml" },
      { href: "/app/cases", label: "nav.cases", icon: Briefcase, perm: "cases.read" },
    ],
  },
  {
    label: "nav.group.assess",
    items: [
      { href: "/app/credit", label: "nav.credit", icon: Scale, perm: "credit.read", module: "credit" },
      { href: "/app/documents", label: "nav.documents", icon: FileSearch, perm: "documents.read", module: "documents" },
      { href: "/app/customers", label: "nav.customers", icon: Users, perm: "customer.read", module: "customer" },
      { href: "/app/forecasting", label: "nav.forecasting", icon: ChartLine, perm: "forecast.read", module: "forecasting" },
    ],
  },
  {
    label: "nav.group.assist",
    items: [
      { href: "/app/copilot", label: "nav.copilot", icon: MessagesSquare, perm: "copilot.use", module: "copilot" },
      { href: "/app/agents", label: "nav.agents", icon: Bot, perm: "agents.use", module: "agents" },
    ],
  },
  {
    label: "nav.group.govern",
    items: [
      { href: "/app/data", label: "nav.data", icon: Database, perm: "data.read", module: "data" },
      { href: "/app/models", label: "nav.models", icon: Boxes, perm: "models.read", module: "governance" },
      { href: "/app/governance", label: "nav.governance", icon: ShieldCheck, perm: "governance.read", module: "governance" },
      { href: "/app/reports", label: "nav.reports", icon: FileText, perm: "reports.read" },
    ],
  },
  {
    label: "nav.group.admin",
    items: [
      { href: "/app/org", label: "nav.org", icon: Building, perm: "org.read" },
      { href: "/app/team", label: "nav.team", icon: UserCog, perm: "org.read" },
      { href: "/app/admin", label: "nav.admin", icon: Building2, perm: "audit.read" },
      { href: "/app/platform", label: "nav.platform", icon: ServerCog, platform: true },
      { href: "/app/settings", label: "nav.settings", icon: Settings },
    ],
  },
];

export function visibleNav(me: Me | undefined): NavGroup[] {
  if (!me) return [];
  const ok = (it: NavItem) =>
    (it.platform ? !!me.user?.is_platform_admin : true) &&
    (!it.perm || me.permissions.includes(it.perm)) &&
    (!it.module || me.modules.includes(it.module));
  return NAV.map((g) => ({ ...g, items: g.items.filter(ok) })).filter((g) => g.items.length);
}

function Sidebar({ me, onNavigate }: { me: Me; onNavigate?: () => void }) {
  const { t } = useI18n();
  const path = usePathname();
  return (
    <nav aria-label="Navigation" className="flex flex-col gap-5 px-3 py-4">
      {visibleNav(me).map((g) => (
        <div key={g.label}>
          <p className="px-2 mb-1 text-xs font-medium text-ink-3">{t(g.label)}</p>
          <ul className="flex flex-col gap-px">
            {g.items.map((it) => {
              const active = it.href === "/app" ? path === "/app" : path.startsWith(it.href);
              const Icon = it.icon;
              return (
                <li key={it.href}>
                  <Link href={it.href} onClick={onNavigate} aria-current={active ? "page" : undefined}
                    className={cn("flex items-center gap-2.5 rounded px-2 py-1.5 text-[13.5px] transition-colors",
                      active ? "bg-accent-soft text-accent font-medium" : "text-ink-2 hover:text-ink hover:bg-surface-2")}>
                    <Icon className="size-4 shrink-0" />
                    {t(it.label)}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function OrgSwitcher({ me }: { me: Me }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const router = useRouter();
  const demo = me.org.kind === "demo";
  const switchTo = async (orgId: string) => {
    await post("/auth/switch-org", { org_id: orgId });
    qc.clear();
    router.push("/app");
    router.refresh();
  };
  return (
    <Dropdown.Root>
      <Dropdown.Trigger className="group flex items-center gap-2.5 rounded border border-line bg-surface pl-0 pr-2 h-9 overflow-hidden max-w-[320px]" aria-label={t("shell.switchOrg")}>
        <span className={cn("h-full w-2 shrink-0", demo ? "demo-hatch" : "bg-accent")} aria-hidden />
        <Landmark className="size-4 text-ink-3 shrink-0" />
        <span className="truncate text-[13px] font-medium text-ink">{me.org.name}</span>
        <ChevronsUpDown className="size-3.5 text-ink-3 shrink-0" />
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content align="start" sideOffset={6} className="z-50 min-w-64 rounded border border-line bg-surface p-1 shadow-[var(--shadow-pop)]">
          <Dropdown.Label className="px-2 py-1.5 text-xs text-ink-3">{t("shell.switchOrg")}</Dropdown.Label>
          {me.memberships.map((m) => (
            <Dropdown.Item key={m.org_id} disabled={m.org_id === me.org.id} onSelect={() => switchTo(m.org_id)}
              className="flex items-center justify-between gap-3 rounded px-2 py-1.5 text-sm text-ink outline-none data-[highlighted]:bg-surface-2 data-[disabled]:opacity-60">
              <span className="truncate">{m.name}</span>
              <span className="text-xs text-ink-3">{m.kind === "demo" ? "DEMO" : m.role}</span>
            </Dropdown.Item>
          ))}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState("light");
  useEffect(() => setTheme(document.documentElement.getAttribute("data-theme") ?? "light"), []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("bv_theme", next);
    } catch {
      /* storage unavailable: theme still applies for this page */
    }
    setTheme(next);
  };
  return [theme, toggle];
}

function TopBar({ me, onMenu, onSearch }: { me: Me; onMenu: () => void; onSearch: () => void }) {
  const { t, locale, setLocale } = useI18n();
  const [theme, toggleTheme] = useTheme();
  const logout = useLogout();
  return (
    <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-bg/95 backdrop-blur px-4 h-14">
      <button className="lg:hidden rounded p-1.5 text-ink-2 hover:bg-surface-2" onClick={onMenu} aria-label="Menu"><Menu className="size-5" /></button>
      <Link href="/app" className="hidden sm:flex items-center gap-2 mr-2 shrink-0">
        <BrandMark />
        <span className="text-[15px] font-semibold tracking-tight text-ink">BANKVERSE</span>
      </Link>
      <OrgSwitcher me={me} />
      <button onClick={onSearch} className="ml-auto md:ml-4 flex items-center gap-2 h-9 rounded border border-line bg-surface px-3 text-[13px] text-ink-3 hover:text-ink md:w-80">
        <Search className="size-4" /><span className="hidden md:inline">{t("shell.search")}</span><span className="md:ml-auto hidden md:inline"><Kbd>Ctrl K</Kbd></span>
      </button>
      <div className="flex items-center gap-1 md:ml-auto">
        <button onClick={() => setLocale(locale === "fr" ? "en" : "fr")} className="rounded px-2 h-8 text-[13px] text-ink-2 hover:bg-surface-2" aria-label={t("shell.language")}>
          {locale === "fr" ? "EN" : "FR"}
        </button>
        <button onClick={toggleTheme} className="rounded p-2 text-ink-2 hover:bg-surface-2" aria-label={theme === "dark" ? t("shell.theme.light") : t("shell.theme.dark")}>
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>
        <Dropdown.Root>
          <Dropdown.Trigger className="flex items-center gap-2 rounded pl-1.5 pr-2 h-9 hover:bg-surface-2" aria-label="Account">
            <span className="grid place-items-center size-7 rounded-full bg-surface-3 text-xs font-semibold text-ink">
              {(me.user?.full_name ?? "?").split(" ").map((w) => w[0]).slice(0, 2).join("")}
            </span>
            <span className="hidden xl:flex flex-col items-start leading-tight">
              <span className="text-[13px] text-ink">{me.user?.full_name}</span>
              <span className="text-[11.5px] text-ink-3">{pick(me.role_label, locale, me.role)}</span>
            </span>
          </Dropdown.Trigger>
          <Dropdown.Portal>
            <Dropdown.Content align="end" sideOffset={6} className="z-50 min-w-56 rounded border border-line bg-surface p-1 shadow-[var(--shadow-pop)]">
              <div className="px-2 py-1.5">
                <p className="text-sm text-ink">{me.user?.full_name}</p>
                <p className="text-xs text-ink-3">{me.user?.email}</p>
                <p className="text-xs text-ink-3 mt-0.5">{pick(me.role_label, locale, me.role)}{me.mfa_verified ? " · MFA" : ""}</p>
              </div>
              <Dropdown.Separator className="my-1 h-px bg-line" />
              <Dropdown.Item asChild className="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-ink outline-none data-[highlighted]:bg-surface-2">
                <Link href="/app/settings"><Settings className="size-4" />{t("nav.settings")}</Link>
              </Dropdown.Item>
              <Dropdown.Item onSelect={logout} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-ink outline-none data-[highlighted]:bg-surface-2">
                <LogOut className="size-4" />{t("shell.logout")}
              </Dropdown.Item>
            </Dropdown.Content>
          </Dropdown.Portal>
        </Dropdown.Root>
      </div>
    </header>
  );
}

export function BrandMark({ className }: { className?: string }) {
  // Monogram: a ledger column (three rules) inside a vault-door square.
  return (
    <svg viewBox="0 0 24 24" className={cn("size-6", className)} aria-hidden>
      <rect x="1.5" y="1.5" width="21" height="21" rx="4" fill="var(--ink)" />
      <path d="M7 8h10M7 12h7M7 16h10" stroke="var(--bg)" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="17.5" cy="12" r="1.6" fill="var(--accent)" />
    </svg>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const { data: me, error, isLoading, refetch } = useMe();
  const router = useRouter();
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearch(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (error && (error.status === 401 || error.code === "mfa_required")) {
      router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
    }
  }, [error, router]);

  if (isLoading || (error && error.status === 401)) return <LoadingBlock className="min-h-screen" />;
  if (error || !me) return <div className="min-h-screen grid place-items-center"><ErrorState error={error} onRetry={() => refetch()} /></div>;
  const demo = me.org.kind === "demo";
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-surface focus:px-3 focus:py-2 rounded">{t("shell.skip")}</a>
      {demo && <div className="demo-hatch h-1" role="presentation" />}
      <TopBar me={me} onMenu={() => setMenu(true)} onSearch={() => setSearch(true)} />
      <div className="flex">
        <aside className="hidden lg:block w-60 shrink-0 border-r border-line min-h-[calc(100vh-56px)] sticky top-14 self-start max-h-[calc(100vh-56px)] overflow-y-auto scroll-thin">
          <Sidebar me={me} />
          {demo && (
            <p className="mx-4 mb-4 rounded border border-demo/40 bg-risk-medium-soft px-2.5 py-2 text-xs leading-snug text-ink-2">
              <Activity className="inline size-3.5 mr-1 text-demo" />{t("shell.demoBanner")}
            </p>
          )}
        </aside>
        {menu && (
          <div className="lg:hidden fixed inset-0 z-40" role="dialog" aria-modal>
            <button className="absolute inset-0 bg-[rgb(13_20_34/0.45)]" onClick={() => setMenu(false)} aria-label={t("common.close")} />
            <aside className="absolute left-0 top-0 bottom-0 w-72 bg-surface border-r border-line overflow-y-auto">
              <Sidebar me={me} onNavigate={() => setMenu(false)} />
            </aside>
          </div>
        )}
        <main id="main" className="flex-1 min-w-0 px-4 md:px-8 py-6 max-w-[1440px]">{children}</main>
      </div>
      <CommandPalette open={search} onOpenChange={setSearch} me={me} />
    </div>
  );
}
