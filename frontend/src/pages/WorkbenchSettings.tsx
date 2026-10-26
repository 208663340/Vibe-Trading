import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { workbenchApi, type WorkbenchInfo } from "@/features/workbench/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getApiAuthKey, setApiAuthKey } from "@/lib/apiAuth";
export function WorkbenchSettings() {
  const { t } = useTranslation();
  const [key, setKey] = useState(getApiAuthKey), [revision, setRevision] = useState(0);
  const [info, setInfo] = useState<WorkbenchInfo | null>(null), [error, setError] = useState("");
  useEffect(() => { let current = true; workbenchApi.info().then(data => { if (current) { setInfo(data); setError(""); } }).catch(err => { if (current) { setInfo(null); setError(String(err)); } }); return () => { current = false; }; }, [revision]);
  return <div className="mx-auto max-w-3xl space-y-6 p-6 lg:p-10"><h1 className="text-2xl font-semibold">{t("workbench.settings")}</h1><p className="text-sm text-muted-foreground">{t("workbench.settingsHint")}</p>
    <form className="space-y-4 rounded-xl border border-border p-6" onSubmit={e => { e.preventDefault(); setApiAuthKey(key); setRevision(v => v + 1); }}>
      <label className="block text-sm">{t("workbench.key")}<Input type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} className="mt-2" /></label>
      <Button type="submit">{t("workbench.save")}</Button>
    </form>
    {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
    {info && <dl className="space-y-4 rounded-xl border border-border p-6">{([["workspace", info.workspace], ["storage", info.runs_dir], ["workers", info.workers]] as const).map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{t(`workbench.${label}`)}</dt><dd className="mt-1 break-all font-mono text-sm">{value}</dd></div>)}</dl>}
  </div>;
}
