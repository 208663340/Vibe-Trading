import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, FlaskConical, RefreshCw, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { activeExperiment, workbenchApi, type Experiment } from "@/features/workbench/api";

export const exampleCommand = "quant-workbench submit examples/workbench/experiment.json";
export function Workbench() {
  const { t, i18n } = useTranslation();
  const [items, setItems] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [project, setProject] = useState(""), [selected, setSelected] = useState<string[]>([]);
  const [revision, setRevision] = useState(0), [pending, setPending] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null), [logs, setLogs] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const data = await workbenchApi.list(controller.signal);
        if (!controller.signal.aborted) { setItems(data); setError(""); }
      } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
      finally { if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(refresh, 3000); } }
    };
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [revision]);
  useEffect(() => {
    let current = true;
    setLogs(null); setCopied(false);
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      if (!detail) return;
      try { const data = await workbenchApi.logs(detail); if (current) setLogs((data.truncated ? "…\n" : "") + data.text); }
      catch (err) { if (current) setLogs(String(err)); }
      finally { if (current) timer = setTimeout(refresh, 3000); }
    };
    void refresh();
    return () => { current = false; clearTimeout(timer); };
  }, [detail, revision]);
  const focused = items.find(item => item.run_id === detail);
  const compareIds = selected.filter(id => items.some(item => item.run_id === id && item.status === "success"));
  const cancel = async (id: string) => {
    setPending(id);
    try { const updated = await workbenchApi.cancel(id); setItems(old => old.map(item => item.run_id === id ? updated : item)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setPending(null); }
  };
  return <div className="mx-auto max-w-6xl space-y-7 p-5 lg:p-10">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="mb-2 font-mono text-xs uppercase tracking-[.18em] text-primary">Quant Research / Experiments</p><h1 className="text-3xl font-semibold tracking-tight">{t("workbench.title")}</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">{t("workbench.subtitle")}</p></div>
      <Button type="button" variant="outline" size="sm" onClick={() => setRevision(v => v + 1)}><RefreshCw className="me-2 inline h-3.5 w-3.5" />{t("workbench.refresh")}</Button>
    </header>
    <div className="grid grid-cols-3 divide-x divide-border rounded-xl border border-border bg-card">
      {([["total", items.length], ["active", items.filter(activeExperiment).length], ["complete", items.filter(i => i.status === "success").length]] as const).map(([key, count]) => <div key={key} className="p-4 sm:p-6"><p className="text-xs text-muted-foreground">{t(`workbench.${key}`)}</p><p className="mt-2 font-mono text-3xl">{loading ? "—" : count}</p></div>)}
    </div>
    {error && <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm">{t("workbench.error")}: {error} <Link className="underline" to="/settings">{t("workbench.settings")}</Link></div>}
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-sm">
          <span id="workbench-project-label">{t("workbench.project")}</span>
          <Select dir={i18n.dir(i18n.language)} value={project ? `project:${project}` : "all"} onValueChange={value => setProject(value === "all" ? "" : value.slice(8))}>
            <SelectTrigger aria-labelledby="workbench-project-label" className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("workbench.all")}</SelectItem>
              {[...new Set(items.map(i => i.project))].sort().map(p => <SelectItem key={p} value={`project:${p}`}>{p}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        {compareIds.length === 2 ? <Button asChild size="sm"><Link to={`/compare?left=${encodeURIComponent(compareIds[0])}&right=${encodeURIComponent(compareIds[1])}`}>{t("workbench.compare")} (2)</Link></Button> : <Button disabled variant="outline" size="sm">{t("workbench.compare")} ({compareIds.length}/2)</Button>}
      </div>
      {loading ? <p role="status" className="py-12 text-center text-muted-foreground">{t("workbench.loading")}</p> : !items.length && !error ? <div className="rounded-xl border border-dashed border-border p-12 text-center"><FlaskConical className="mx-auto mb-4 h-8 w-8 text-primary" /><h2 className="text-lg font-medium">{t("workbench.empty")}</h2><p className="mt-2 text-sm text-muted-foreground">{t("workbench.emptyHint")}</p></div> : items.filter(i => !project || i.project === project).map(item => <article key={item.run_id} className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start gap-3"><Checkbox className="mt-1" aria-label={t("workbench.select", { name: item.name })} checked={compareIds.includes(item.run_id)} disabled={item.status !== "success" || (compareIds.length >= 2 && !compareIds.includes(item.run_id))} onCheckedChange={checked => setSelected(old => checked === true ? [...old, item.run_id] : old.filter(id => id !== item.run_id))} />
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-medium">{item.name}</h2><Badge variant={item.status === "failed" || item.status === "interrupted" ? "destructive" : item.status === "success" ? "success" : activeExperiment(item) ? "default" : "secondary"}>{t(`workbench.status.${item.status}`)}</Badge></div>
            <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{item.project} · {item.run_id} · {new Date(item.created_at).toLocaleString()}</p>
            {item.change_note && <p className="mt-3 text-sm">{item.change_note}</p>}
            {item.reason && <p className="mt-3 whitespace-pre-wrap break-words text-sm text-destructive">{item.reason}</p>}
            {item.checks?.artifact_schema === "passed" && <p className="mt-3 text-xs text-muted-foreground">{t("workbench.schema")}</p>}
            <div className="mt-4 flex flex-wrap gap-2">
              {item.status === "success" && <Button asChild variant="outline" size="sm"><Link to={`/runs/${item.run_id}?view=dashboard`}>{t("workbench.report")} <ArrowUpRight /></Link></Button>}
              <Button type="button" variant="outline" size="sm" onClick={() => setDetail(detail === item.run_id ? null : item.run_id)} aria-expanded={detail === item.run_id}>{t("workbench.logs")}</Button>
              {activeExperiment(item) && <Button type="button" variant="outline" size="sm" disabled={pending === item.run_id || item.status === "cancelling"} onClick={() => void cancel(item.run_id)}>{t("workbench.cancel")}</Button>}
            </div>
            {focused?.run_id === item.run_id && <div className="mt-5 space-y-4 border-t border-border pt-4">
              <Button variant="outline" size="sm" onClick={async () => { try { await navigator.clipboard.writeText(JSON.stringify(focused, null, 2)); setCopied(true); } catch (err) { setError(String(err)); } }}>{t(copied ? "workbench.copied" : "workbench.copy")}</Button>
              {item.parent_run_id && <p className="text-xs">{t("workbench.parent")}: <Link to={`/runs/${item.parent_run_id}`} className="underline">{item.parent_run_id}</Link></p>}
              <h3 className="text-sm font-medium">{t("workbench.config")}</h3><pre className="max-h-64 overflow-auto rounded bg-muted p-3 text-xs">{JSON.stringify(item.config, null, 2)}</pre>
              <h3 className="text-sm font-medium">{t("workbench.snapshot")}</h3><ul className="space-y-1 break-all font-mono text-xs text-muted-foreground">{item.files.map(file => <li key={file.path}>{file.path} · SHA256 {file.sha256}</li>)}</ul>
              <pre aria-live="polite" className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-3 text-xs">{logs === null ? t("workbench.loading") : logs || t("workbench.noLogs")}</pre>
            </div>}
          </div>
        </div>
      </article>)}
    </section>
    <details open={!loading && !items.length} className="rounded-xl border border-border bg-card p-5"><summary className="cursor-pointer text-sm font-medium"><Terminal className="me-2 inline h-4 w-4 text-primary" />{t("workbench.handoff")}</summary><p className="mt-3 text-sm leading-6 text-muted-foreground">{t("workbench.handoffHint")}</p><pre className="mt-3 overflow-auto rounded bg-muted p-4 text-xs">{exampleCommand}</pre><Link to="/settings" className="mt-3 inline-block text-sm text-primary underline">{t("workbench.settings")}</Link></details>
  </div>;
}
