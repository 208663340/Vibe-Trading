import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import i18n from "@/i18n";
import { Layout } from "../Layout";
beforeEach(async () => { localStorage.clear(); vi.clearAllMocks(); await i18n.changeLanguage("en"); });
afterEach(() => vi.unstubAllGlobals());
function mount() { return render(<MemoryRouter><Routes><Route element={<Layout />}><Route path="/" element={<p>Experiments content</p>} /></Route></Routes></MemoryRouter>); }
it("opens the research workspace without loading conversations", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  mount();
  expect(screen.getByRole("main")).toHaveTextContent("Experiments content");
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "Quant Workbench" })).toHaveAttribute("href", "/");
  expect(screen.queryByText("Vibe-Trading")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Research workbench" })).toHaveAttribute("href", "/");
  expect(screen.queryByText("New chat")).not.toBeInTheDocument();
});
it("keeps accessible navigation after collapsing", () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: /Collapse/i }));
  expect(screen.getByRole("link", { name: "Backtest reports" })).toHaveAttribute("href", "/reports");
  expect(screen.getByRole("button", { name: /Expand/i })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Skip to main/ })).toHaveAttribute("href", "#main-content");
});
