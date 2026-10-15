import type { ComponentType } from "react";

type PageLoader = () => Promise<{ default: ComponentType }>;

export const loadHome = () => import("@/pages/Home").then((module) => ({ default: module.Home }));
export const loadAgent = () => import("@/pages/Agent").then((module) => ({ default: module.Agent }));
export const loadRunDetail = () => import("@/pages/RunDetail").then((module) => ({ default: module.RunDetail }));
export const loadCompare = () => import("@/pages/Compare").then((module) => ({ default: module.Compare }));
export const loadSettings = () => import("@/pages/Settings").then((module) => ({ default: module.Settings }));
export const loadRuntime = () => import("@/pages/Runtime").then((module) => ({ default: module.Runtime }));
export const loadScheduled = () => import("@/pages/Scheduled").then((module) => ({ default: module.Scheduled }));
export const loadReports = () => import("@/pages/Reports").then((module) => ({ default: module.Reports }));
export const loadPortfolio = () => import("@/pages/Portfolio").then((module) => ({ default: module.Portfolio }));
export const loadCorrelation = () => import("@/pages/Correlation").then((module) => ({ default: module.Correlation }));
export const loadAlphaZoo = () => import("@/pages/AlphaZoo").then((module) => ({ default: module.AlphaZoo }));
export const loadOptionsLab = () => import("@/pages/OptionsLab").then((module) => ({ default: module.OptionsLab }));

const routeLoaders: Record<string, PageLoader> = {
  "/": loadAgent,
  "/about": loadHome,
  "/agent": loadAgent,
  "/runtime": loadRuntime,
  "/scheduled": loadScheduled,
  "/reports": loadReports,
  "/portfolio": loadPortfolio,
  "/settings": loadSettings,
  "/compare": loadCompare,
  "/correlation": loadCorrelation,
  "/options": loadOptionsLab,
  "/alpha-zoo": loadAlphaZoo,
};

export function preloadRoute(path: string) {
  const load = routeLoaders[path];
  if (load) void load();
}
