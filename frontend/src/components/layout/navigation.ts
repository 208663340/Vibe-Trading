import { FlaskConical, FileText, GitCompareArrows, Settings } from "lucide-react";
export const NAVIGATION_GROUPS = [
  { id: "research", items: [
    { id: "workbench", to: "/", icon: FlaskConical },
    { id: "reports", to: "/reports", icon: FileText },
    { id: "compare", to: "/compare", icon: GitCompareArrows },
  ] },
  { id: "automation", items: [{ id: "settings", to: "/settings", icon: Settings }] },
] as const;
export type NavigationItem = typeof NAVIGATION_GROUPS[number]["items"][number];
export function isNavigationActive(to: string, pathname: string): boolean {
  return to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`);
}
