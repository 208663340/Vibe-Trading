import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState } from "react";
import { Link, Outlet } from "react-router";
import { Check, ChevronDown, Languages, Moon, Sun, ChevronsLeft, ChevronsRight, FlaskConical } from "lucide-react";
import { cn } from "@/shared/cn";
import { useDarkMode } from "@/hooks/useDarkMode";
import { safeGet, safeSet } from "@/shared/storage";
import { SUPPORTED_LANGUAGES } from "@/i18n";
import { SidebarNavigation } from "./SidebarNavigation";

export function Layout() {
  const { t } = useTranslation();
  const { dark, toggle } = useDarkMode();
  const [collapsed, setCollapsed] = useState(() => safeGet("qa-sidebar") === "collapsed");
  useEffect(() => {
    const sync = (event: StorageEvent) => { if (event.key === "qa-sidebar" || event.key === null) setCollapsed(safeGet("qa-sidebar") === "collapsed"); };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  return <div className="flex h-dvh overflow-hidden bg-background text-foreground">
    <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:z-50 focus:bg-background focus:p-3">{t("layout.skipToMain")}</a>
    <aside aria-label={t("layout.sidebar")} className={cn("flex shrink-0 flex-col border-e border-border bg-card transition-[width] max-md:w-12", collapsed ? "w-14" : "w-56")}>
      <Link to="/" aria-label={t("workbench.appName")} className="flex h-16 items-center justify-center gap-2 px-3"><FlaskConical className="h-6 w-6 shrink-0 text-primary" aria-hidden="true" />{!collapsed && <span className="text-sm font-semibold tracking-tight max-md:hidden">{t("workbench.appName")}</span>}</Link>
      <SidebarNavigation collapsed={collapsed} />
      <div className="mt-auto space-y-2 border-t border-border p-2">
        <div className={cn("flex items-center", collapsed ? "flex-col" : "justify-between max-md:flex-col")}>
          <LanguageSwitcher compact={collapsed} />
          <button type="button" onClick={toggle} aria-label={t(dark ? "layout.light" : "layout.dark")} className="rounded p-2 hover:bg-muted">{dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}</button>
        </div>
        <button type="button" aria-label={t(collapsed ? "layout.expand" : "layout.collapse")} onClick={() => { const next = !collapsed; setCollapsed(next); safeSet("qa-sidebar", next ? "collapsed" : "expanded"); }} className="flex w-full justify-center rounded p-2 text-muted-foreground hover:bg-muted max-md:hidden">{collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}</button>
      </div>
    </aside>
    <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 overflow-auto"><Outlet /></main>
  </div>;
}

function LanguageSwitcher({ compact = false }: { compact?: boolean }) {
  const { i18n, t } = useTranslation();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const [menuStyle, setMenuStyle] = useState<{ left: number; bottom: number; minWidth: number } | null>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent | TouchEvent) => {
      if (
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node) &&
        !(e.target as HTMLElement).closest?.("[data-lang-menu]")
      ) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("touchstart", onClick, { passive: true });
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("touchstart", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Recompute the menu's fixed coordinates whenever it opens, or whenever
  // the viewport changes (resize / scroll / language switch). The menu is
  // anchored to the trigger's *left edge* and sits *above* the trigger.
  useEffect(() => {
    if (!open || !triggerRef.current) return;
    const place = () => {
      const r = triggerRef.current?.getBoundingClientRect();
      if (!r) return;
      // Anchor: align the menu's right edge with the trigger's right edge,
      // then clamp to the viewport so the menu never overflows the screen.
      const menuWidth = 160; // px — approx longest label "العربية" + padding
      const gap = 4; // mb-1
      const desiredLeft = r.right - menuWidth;
      const maxLeft = window.innerWidth - menuWidth - 8;
      const minLeft = 8;
      const left = Math.max(minLeft, Math.min(maxLeft, desiredLeft));
      setMenuStyle({
        left,
        // distance from viewport bottom: viewport height − trigger top + gap
        bottom: window.innerHeight - r.top + gap,
        minWidth: menuWidth,
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  // i18n.language (singular) is the primary active language. We try an exact
  // match first against SUPPORTED_LANGUAGES. If that fails (e.g. a regional
  // variant like "ja-JP"), we fall back to i18n.languages (plural) which
  // includes both the detected and resolved codes. NOTE: i18n.languages
  // always contains the fallback language ("en"), so it must NOT be the
  // primary match — otherwise "en" being first in SUPPORTED_LANGUAGES
  // would always win and the switcher would never show any other language.
  const current =
    SUPPORTED_LANGUAGES.find((l) => l.code === i18n.language) ??
    SUPPORTED_LANGUAGES.find((l) => i18n.languages?.includes(l.code)) ??
    SUPPORTED_LANGUAGES[0];

  return (
    <div>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={t("layout.language")}
        className="flex items-center gap-1 p-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors max-md:justify-center"
      >
        <Languages className="h-3.5 w-3.5 shrink-0" />
        {!compact && <span className="whitespace-nowrap max-md:hidden">{current.label}</span>}
        {!compact && <ChevronDown className={cn("h-3 w-3 shrink-0 transition-transform max-md:hidden", open && "rotate-180")} />}
      </button>
      {open && menuStyle && (
        <ul
          data-lang-menu
          aria-label="Select language"
          style={{
            position: "fixed",
            left: menuStyle.left,
            bottom: menuStyle.bottom,
            minWidth: menuStyle.minWidth,
            zIndex: 60,
          }}
          className="rounded-md border border-border/60 bg-popover shadow-lg ring-1 ring-black/5"
        >
          {SUPPORTED_LANGUAGES.map((lang) => {
            const active = lang.code === current.code;
            return (
              <li key={lang.code}>
                <button
                  type="button"
                  onClick={() => {
                    i18n.changeLanguage(lang.code).catch(console.error);
                    setOpen(false);
                  }}
                  aria-current={active || undefined}
                  className={cn(
                    "w-full flex items-center gap-2 px-2.5 py-1.5 text-xs hover:bg-muted hover:text-foreground transition-colors",
                    active && "text-foreground",
                  )}
                >
                  <span className="flex-1 text-start whitespace-nowrap">{lang.label}</span>
                  {active && <Check className="h-3 w-3 shrink-0" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
