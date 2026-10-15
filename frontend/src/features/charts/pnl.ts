/**
 * P&L semantic colors for the Options Lab charts.
 *
 * getChartTheme().upColor/downColor are DIRECTION colors — they flip under the
 * Chinese red-up convention. P&L must read green=profit / red=loss in every
 * locale, so this helper reads the raw --success/--danger design tokens
 * instead (same CSS variables chart-theme parses, but unflipped).
 */

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function hslToHex(hsl: string): string {
  if (!hsl) return "";
  const [h, s, l] = hsl.split(/\s+/).map(parseFloat);
  if (isNaN(h)) return "";
  const a = (s / 100) * Math.min(l / 100, 1 - l / 100);
  const f = (n: number) => {
    const kk = (n + h / 30) % 12;
    const color = l / 100 - a * Math.max(Math.min(kk - 3, 9 - kk, 1), -1);
    return Math.round(255 * color).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

export interface PnlColors {
  profit: string;
  loss: string;
  neutral: string;
}

export function getPnlColors(): PnlColors {
  const isDark = document.documentElement.classList.contains("dark");
  return {
    profit: hslToHex(cssVar("--success")) || "#22c55e",
    loss: hslToHex(cssVar("--danger")) || "#ef4444",
    neutral: isDark ? "#3a4252" : "#eef0f2",
  };
}
