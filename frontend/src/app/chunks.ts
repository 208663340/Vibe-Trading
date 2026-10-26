import type { ComponentType } from "react";
type PageLoader = () => Promise<{ default: ComponentType }>;
export const loadWorkbench = () => import("@/pages/Workbench").then(m => ({ default: m.Workbench }));
export const loadRunDetail = () => import("@/pages/RunDetail").then(m => ({ default: m.RunDetail }));
export const loadCompare = () => import("@/pages/Compare").then(m => ({ default: m.Compare }));
export const loadSettings = () => import("@/pages/WorkbenchSettings").then(m => ({ default: m.WorkbenchSettings }));
export const loadReports = () => import("@/pages/Reports").then(m => ({ default: m.Reports }));
const routeLoaders: Record<string, PageLoader> = { "/": loadWorkbench, "/reports": loadReports, "/settings": loadSettings, "/compare": loadCompare };
export function preloadRoute(path: string) { const load = routeLoaders[path]; if (load) void load(); }
