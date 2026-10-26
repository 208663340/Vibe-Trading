import i18n from "@/i18n";
import { attributeDrawdownLosses, type DrawdownEpisode } from "@/features/runs/tearsheet";

interface Props {
  episodes: DrawdownEpisode[];
  trades: Array<Record<string, string>> | undefined;
}

function money(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export function LossPointsPanel({ episodes, trades }: Props) {
  if (episodes.length === 0) return null;
  if (!trades || trades.length === 0) {
    return <p className="px-4 py-3 text-sm text-muted-foreground">{i18n.t("runDetail.lossNoTrades")}</p>;
  }
  const reports = attributeDrawdownLosses(episodes, trades).slice(0, 3);
  return (
    <div className="space-y-4 px-4 py-3">
      <p className="text-xs text-muted-foreground">{i18n.t("runDetail.lossPointsNote")}</p>
      {reports.map((report, index) => (
        <section key={`${report.episode.peakTime}-${index}`} className="space-y-2">
          <p className="text-sm font-medium">
            {index + 1}. {(report.episode.depth * 100).toFixed(2)}%
            <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">
              {report.episode.peakTime} → {report.episode.troughTime}
            </span>
          </p>
          {report.losingCount === 0 ? (
            <p className="text-xs text-muted-foreground">{i18n.t("runDetail.lossNoTrades")}</p>
          ) : (
            <>
              {report.clusters.length > 0 && (
                <div className="overflow-x-auto">
                  <p className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">{i18n.t("runDetail.lossCluster")}</p>
                  <table className="w-full text-sm">
                    <tbody>
                      {report.clusters.slice(0, 3).map((cluster) => (
                        <tr key={cluster.date} className="border-b last:border-0">
                          <td className="py-1.5 pr-4 font-mono text-xs">{cluster.date}</td>
                          <td className="py-1.5 pr-4 font-mono tabular-nums">{i18n.t("runDetail.lossCount", { count: cluster.count })}</td>
                          <td className="py-1.5 font-mono tabular-nums text-danger">{money(cluster.pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="overflow-x-auto">
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">{i18n.t("runDetail.lossWorst")}</p>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                      <th className="py-1.5 pr-4 font-medium">{i18n.t("runDetail.lossWhen")}</th>
                      <th className="py-1.5 pr-4 font-medium">{i18n.t("runDetail.lossCode")}</th>
                      <th className="py-1.5 pr-4 font-medium">{i18n.t("runDetail.lossPnl")}</th>
                      <th className="py-1.5 font-medium">{i18n.t("runDetail.lossReason")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.worst.map((trade) => (
                      <tr key={`${trade.time}-${trade.code}-${trade.pnl}`} className="border-b last:border-0">
                        <td className="py-1.5 pr-4 font-mono text-xs">{trade.time}</td>
                        <td className="py-1.5 pr-4 font-mono text-xs">{trade.code}</td>
                        <td className="py-1.5 pr-4 font-mono tabular-nums text-danger">{money(trade.pnl)}</td>
                        <td className="py-1.5 text-xs text-muted-foreground">{trade.reason || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      ))}
    </div>
  );
}
