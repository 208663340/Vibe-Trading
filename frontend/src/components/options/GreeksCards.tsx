import { useTranslation } from "react-i18next";
import { cn } from "@/shared/cn";
import type { OptionsGreeks } from "@/features/options/model";

type Tone = "positive" | "negative" | "warning" | "neutral";

const TONE_CLS: Record<Tone, string> = {
  positive: "text-success",
  negative: "text-danger",
  warning: "text-warning",
  neutral: "text-foreground",
};

function GreekCard({ label, value, desc, tone }: { label: string; value: string; desc: string; tone: Tone }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-1 truncate text-lg font-semibold tabular-nums", TONE_CLS[tone])}>{value}</div>
      <div className="mt-1 text-xs leading-snug text-muted-foreground">{desc}</div>
    </div>
  );
}

interface Props {
  greeks: OptionsGreeks;
}

export function GreeksCards({ greeks }: Props) {
  const { t } = useTranslation();

  const signTone = (v: number): Tone => (v > 0 ? "positive" : v < 0 ? "negative" : "neutral");

  return (
    <section>
      <div className="mb-2 text-sm font-semibold">{t("options.greeks.title")}</div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <GreekCard
          label={t("options.greeks.delta")}
          value={greeks.delta.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          desc={t("options.greeks.deltaDesc")}
          tone={signTone(greeks.delta)}
        />
        <GreekCard
          label={t("options.greeks.gamma")}
          value={greeks.gamma.toLocaleString(undefined, { maximumFractionDigits: 4 })}
          desc={t("options.greeks.gammaDesc")}
          tone="neutral"
        />
        <GreekCard
          label={t("options.greeks.theta")}
          value={greeks.theta.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          desc={t("options.greeks.thetaDesc")}
          tone={greeks.theta < 0 ? "warning" : signTone(greeks.theta)}
        />
        <GreekCard
          label={t("options.greeks.vega")}
          value={greeks.vega.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          desc={t("options.greeks.vegaDesc")}
          tone={signTone(greeks.vega)}
        />
        <GreekCard
          label={t("options.greeks.rho")}
          value={greeks.rho.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          desc={t("options.greeks.rhoDesc")}
          tone="neutral"
        />
      </div>
    </section>
  );
}
