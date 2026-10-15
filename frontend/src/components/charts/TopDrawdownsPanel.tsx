import i18n from "@/i18n";
import type { DrawdownEpisode } from "@/features/runs/tearsheet";

interface Props {
  episodes: DrawdownEpisode[];
}

export function TopDrawdownsPanel({ episodes }: Props) {
  if (episodes.length === 0) {
    return <div className="text-muted-foreground text-sm p-4">{i18n.t("runDetail.noTearsheetData")}</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground [&_th]:font-medium">
            <th className="py-2 ps-4 pr-4">{i18n.t("runDetail.ddRank")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.ddDepth")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.ddPeak")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.ddTrough")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.ddRecovery")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.ddPeakToTrough")}</th>
            <th className="py-2">{i18n.t("runDetail.ddTroughToRecovery")}</th>
          </tr>
        </thead>
        <tbody>
          {episodes.map((ep, i) => (
            <tr key={`${ep.peakTime}-${i}`} className="border-b last:border-0 hover:bg-muted/40">
              <td className="py-2 ps-4 pr-4 font-mono tabular-nums text-muted-foreground">{i + 1}</td>
              <td className="py-2 pr-4 font-mono tabular-nums text-danger">{(ep.depth * 100).toFixed(2)}%</td>
              <td className="py-2 pr-4 font-mono text-xs">{ep.peakTime}</td>
              <td className="py-2 pr-4 font-mono text-xs">{ep.troughTime}</td>
              <td className="py-2 pr-4 font-mono text-xs">{ep.recoveryTime ?? i18n.t("runDetail.ddNotRecovered")}</td>
              <td className="py-2 pr-4 font-mono tabular-nums">
                {ep.peakToTroughDays != null ? i18n.t("runDetail.ddDays", { count: ep.peakToTroughDays }) : "—"}
              </td>
              <td className="py-2 font-mono tabular-nums">
                {ep.troughToRecoveryDays != null ? i18n.t("runDetail.ddDays", { count: ep.troughToRecoveryDays }) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
