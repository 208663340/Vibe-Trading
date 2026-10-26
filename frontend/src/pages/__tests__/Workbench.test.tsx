import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import i18n from "@/i18n";
import { Workbench } from "../Workbench";
const item = { run_id: "run_one", name: "Baseline", project: "ETF", status: "success", created_at: "2026-10-26T00:00:00Z", config: {mode: "program"}, files: [{path: "strategy.py",sha256: "abc",size_bytes: 10}], checks: {artifact_schema: "passed"}, parent_run_id: null, change_note: "", execution_source: "program" };
let items: unknown[];
const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
  const value = path.endsWith("/cancel") ? {...item, status: "cancelling"} : path.endsWith("/logs") ? {text: "traceback detail",truncated: false} : items;
  return new Response(JSON.stringify(value), {status: 200, headers:{"Content-Type":"application/json"}});
});
beforeEach(async () => { items = [item]; fetchMock.mockClear(); vi.stubGlobal("fetch", fetchMock); await i18n.changeLanguage("en"); });
afterEach(() => vi.unstubAllGlobals());
function mount() { render(<MemoryRouter><Workbench /></MemoryRouter>); }
it("opens saved reports and exposes snapshot evidence from the service contract", async () => {
  mount();
  expect(await screen.findByRole("link", {name: /Open report/})).toHaveAttribute("href", "/runs/run_one?view=dashboard");
  fireEvent.click(screen.getByRole("button", {name:"Logs & configuration"}));
  expect(await screen.findByText("traceback detail")).toBeVisible();
  expect(screen.getByText(/SHA256 abc/)).toBeVisible();
  expect(screen.getByText(/accounting \/ out-of-sample not checked/)).toBeVisible();
  expect(fetchMock.mock.calls.every(([path]) => path.startsWith("/api/experiments"))).toBe(true);
});
it("passes the selected experiment IDs to comparison", async () => {
  items = [item,{...item,run_id:"run_two",name:"Revision"}]; mount();
  fireEvent.click(await screen.findByRole("checkbox",{name:"Select Baseline for comparison"}));
  fireEvent.click(screen.getByRole("checkbox",{name:"Select Revision for comparison"}));
  expect(screen.getByRole("link",{name:/Compare selected/})).toHaveAttribute("href","/compare?left=run_one&right=run_two");
});
it("requests cancellation and waits for the server's terminal status", async () => {
  items=[{...item,status:"running"}]; mount();
  fireEvent.click(await screen.findByRole("button",{name:"Cancel experiment"}));
  expect(await screen.findByText("Cancelling")).toBeVisible();
  expect(screen.getByRole("button",{name:"Cancel experiment"})).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledWith("/api/experiments/run_one/cancel", expect.objectContaining({method:"POST"}));
});
it("shows failures and the empty state without fabricated results", async () => {
  items=[];mount();expect(await screen.findByText("No experiments yet")).toBeVisible();
  items=[{...item,status:"failed",reason:"Invalid output"}];fireEvent.click(screen.getByRole("button",{name:"Refresh"}));
  expect(await screen.findByText("Invalid output")).toBeVisible();
  expect(screen.queryByRole("link",{name:/Open report/})).not.toBeInTheDocument();
});
it("surfaces a connection failure", async () => {
  fetchMock.mockRejectedValueOnce(new TypeError("offline"));mount();
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Unable to load experiments"));
});

it("updates an open log tail when the experiment finishes", async () => {
  vi.useFakeTimers();
  let text = "running output";
  items = [{...item, status:"running"}];
  fetchMock.mockImplementation(async (path: string) => new Response(JSON.stringify(path.endsWith("/logs") ? {text,truncated:false} : items), {headers:{"Content-Type":"application/json"}}));
  try {
    mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByRole("button", {name:"Logs & configuration"}));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText("running output")).toBeVisible();
    text="final output"; items=[item];
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(screen.getByText("final output")).toBeVisible();
  } finally { vi.useRealTimers(); }
});

it("filters projects through the keyboard-accessible picker, including a project named all", async () => {
  items=[item,{...item,run_id:"run_other",name:"Other project",project:"all"}];
  const originalScroll = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = () => {};
  try {
    mount();
    await screen.findByText("Other project");
    const trigger=screen.getByRole("combobox",{name:"Project"});
    trigger.focus();
    fireEvent.keyDown(trigger,{key:"ArrowDown"});
    const option=await screen.findByRole("option",{name:"all",exact:true});
    option.focus();
    fireEvent.keyDown(option,{key:"Enter"});
    await waitFor(() => expect(screen.queryByText("Baseline")).not.toBeInTheDocument());
    expect(screen.getByText("Other project")).toBeVisible();
    fireEvent.keyDown(trigger,{key:"ArrowDown"});
    const all=await screen.findByRole("option",{name:"All projects"});
    all.focus();
    fireEvent.keyDown(all,{key:"Enter"});
    expect(await screen.findByText("Baseline")).toBeVisible();
  } finally { HTMLElement.prototype.scrollIntoView = originalScroll; }
});
