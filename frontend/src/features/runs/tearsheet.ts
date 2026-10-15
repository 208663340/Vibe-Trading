import type { EquityPoint } from "@/lib/api";

/** Equity row normalized to finite numbers, chronological order. */
export interface NormalizedEquityPoint {
  time: string;
  equity: number;
  drawdown: number;
}

/** Calendar-month return; null when not computable (gap month or bad base). */
export interface MonthlyReturn {
  year: number;
  /** 1-12 */
  month: number;
  ret: number | null;
}

export interface AnnualReturn {
  year: number;
  ret: number;
}

/** One drawdown episode detected by the running-peak algorithm. */
export interface DrawdownEpisode {
  peakTime: string;
  troughTime: string;
  recoveryTime: string | null;
  /** Negative fraction, e.g. -0.15 = -15%. */
  depth: number;
  peakToTroughDays: number | null;
  troughToRecoveryDays: number | null;
}

/** Highlight rectangle over the equity chart for one drawdown episode. */
export interface DrawdownZone {
  startTime: string;
  endTime: string;
  rank: number;
}

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Safari-safe date parse: "YYYY-MM-DD HH:MM:SS" -> "YYYY-MM-DDTHH:MM:SS".
 *
 * A bare "YYYY-MM-DD" is specified to parse as UTC midnight while every getter
 * below (`getFullYear`, `getMonth`) reads local time, so west of UTC the whole
 * series shifts back a day and each month-boundary bar lands in the previous
 * month. Daily backtests write exactly that shape — pandas drops `00:00:00`
 * when every bar is midnight — so pin date-only input to local midnight, which
 * is also how the "YYYY-MM-DD HH:MM:SS" form already parses.
 */
function parseTime(time: string): Date | null {
  const normalized = DATE_ONLY.test(time) ? `${time}T00:00:00` : time.replace(" ", "T");
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

function monthIndex(year: number, month: number): number {
  return year * 12 + (month - 1);
}

/**
 * Normalize equity rows from either the full `artifacts_equity_csv` payload
 * (string rows keyed timestamp/equity/drawdown) or the capped `equity_curve`
 * (EquityPoint[]). Drops rows with missing time or non-finite equity,
 * preserves input order unless it is visibly reversed (defensive sort).
 */
export function normalizeEquitySeries(
  input: Array<Record<string, string>> | EquityPoint[] | undefined | null,
): NormalizedEquityPoint[] {
  if (!input || input.length === 0) return [];
  const out: NormalizedEquityPoint[] = [];
  for (const row of input) {
    const rec = row as Record<string, unknown>;
    const rawTime = rec.time ?? rec.timestamp;
    const time = rawTime == null ? "" : String(rawTime).trim();
    if (!time) continue;
    const equity = Number(rec.equity);
    if (!Number.isFinite(equity)) continue;
    const dd = Number(rec.drawdown);
    out.push({ time, equity, drawdown: Number.isFinite(dd) ? dd : 0 });
  }
  if (out.length >= 2) {
    const first = parseTime(out[0].time);
    const last = parseTime(out[out.length - 1].time);
    if (first && last && first.getTime() > last.getTime()) {
      out.sort((a, b) => {
        const da = parseTime(a.time)?.getTime() ?? Number.POSITIVE_INFINITY;
        const db = parseTime(b.time)?.getTime() ?? Number.POSITIVE_INFINITY;
        return da - db;
      });
    }
  }
  return out;
}

/**
 * Tearsheet monthly returns: group by calendar month, return =
 * lastEquityOfMonth / prevMonthLastEquity - 1. The first populated month uses
 * the series' first observation as base. Calendar months strictly between the
 * first and last populated month are emitted with ret: null when empty or when
 * the prior calendar month has no data / a non-positive base. No padding
 * before the first or after the last populated month.
 */
export function computeMonthlyReturns(points: NormalizedEquityPoint[]): MonthlyReturn[] {
  if (points.length === 0) return [];
  const monthEnd = new Map<number, number>();
  let firstIdx: number | null = null;
  let lastIdx: number | null = null;
  let seriesBase: number | undefined;
  for (const p of points) {
    const d = parseTime(p.time);
    if (!d) continue;
    if (seriesBase === undefined) seriesBase = p.equity;
    const idx = monthIndex(d.getFullYear(), d.getMonth() + 1);
    monthEnd.set(idx, p.equity);
    if (firstIdx === null || idx < firstIdx) firstIdx = idx;
    if (lastIdx === null || idx > lastIdx) lastIdx = idx;
  }
  if (firstIdx === null || lastIdx === null) return [];
  const result: MonthlyReturn[] = [];
  for (let idx = firstIdx; idx <= lastIdx; idx += 1) {
    const year = Math.floor(idx / 12);
    const month = (idx % 12) + 1;
    const endEquity = monthEnd.get(idx);
    if (endEquity === undefined) {
      result.push({ year, month, ret: null });
      continue;
    }
    const base = idx === firstIdx ? seriesBase : monthEnd.get(idx - 1);
    if (base === undefined || !Number.isFinite(base) || base <= 0 || !Number.isFinite(endEquity)) {
      result.push({ year, month, ret: null });
    } else {
      result.push({ year, month, ret: endEquity / base - 1 });
    }
  }
  return result;
}

/**
 * Tearsheet annual returns: return = lastEquityOfYear / prevYearLastEquity - 1.
 * The first populated year uses the series' first observation as base. Years
 * whose current or prior calendar-year end is missing, or whose base is
 * non-positive, are skipped entirely.
 */
export function computeAnnualReturns(points: NormalizedEquityPoint[]): AnnualReturn[] {
  if (points.length === 0) return [];
  const yearEnd = new Map<number, number>();
  let seriesBase: number | undefined;
  for (const p of points) {
    const d = parseTime(p.time);
    if (!d) continue;
    if (seriesBase === undefined) seriesBase = p.equity;
    yearEnd.set(d.getFullYear(), p.equity);
  }
  if (yearEnd.size === 0) return [];
  const years = [...yearEnd.keys()].sort((a, b) => a - b);
  const result: AnnualReturn[] = [];
  for (let i = 0; i < years.length; i += 1) {
    const year = years[i];
    const endEquity = yearEnd.get(year);
    const base = i === 0 ? seriesBase : yearEnd.get(year - 1);
    if (
      endEquity === undefined || !Number.isFinite(endEquity)
      || base === undefined || !Number.isFinite(base) || base <= 0
    ) {
      continue;
    }
    result.push({ year, ret: endEquity / base - 1 });
  }
  return result;
}

function daySpan(fromTime: string, toTime: string): number | null {
  const from = parseTime(fromTime);
  const to = parseTime(toTime);
  if (!from || !to) return null;
  return Math.round((to.getTime() - from.getTime()) / 86_400_000);
}

/**
 * Top-N drawdown episodes via the running-peak algorithm. An episode starts at
 * the first bar below the running peak (peakTime = the peak bar's time), ends
 * when equity >= the episode's peak level (recoveryTime = that bar's time), or
 * stays open (recoveryTime = null) if the series ends mid-episode.
 * Depth = trough/peak - 1. Sorted deepest first, capped at n.
 */
export function computeTopDrawdowns(points: NormalizedEquityPoint[], n = 5): DrawdownEpisode[] {
  if (points.length < 2 || n <= 0) return [];
  const episodes: DrawdownEpisode[] = [];
  let peakEquity = Number.NEGATIVE_INFINITY;
  let peakTime = "";
  let inEpisode = false;
  let epPeak = 0;
  let epPeakTime = "";
  let trough = 0;
  let troughTime = "";

  const closeEpisode = (recoveryTime: string | null) => {
    const depth = epPeak > 0 ? trough / epPeak - 1 : 0;
    episodes.push({
      peakTime: epPeakTime,
      troughTime,
      recoveryTime,
      depth,
      peakToTroughDays: daySpan(epPeakTime, troughTime),
      troughToRecoveryDays: recoveryTime ? daySpan(troughTime, recoveryTime) : null,
    });
    inEpisode = false;
  };

  for (const p of points) {
    if (p.equity > peakEquity) {
      peakEquity = p.equity;
      peakTime = p.time;
    }
    if (p.equity < peakEquity) {
      if (!inEpisode) {
        inEpisode = true;
        epPeak = peakEquity;
        epPeakTime = peakTime;
        trough = p.equity;
        troughTime = p.time;
      } else if (p.equity < trough) {
        trough = p.equity;
        troughTime = p.time;
      }
    } else if (inEpisode && p.equity >= epPeak) {
      closeEpisode(p.time);
    }
  }
  if (inEpisode) closeEpisode(null);

  episodes.sort((a, b) => a.depth - b.depth);
  return episodes.slice(0, n);
}

/** Map episodes to equity-chart zones; unrecovered episodes run to lastTime. */
export function toDrawdownZones(episodes: DrawdownEpisode[], lastTime: string): DrawdownZone[] {
  return episodes.map((ep, i) => ({
    startTime: ep.peakTime,
    endTime: ep.recoveryTime ?? lastTime,
    rank: i + 1,
  }));
}
