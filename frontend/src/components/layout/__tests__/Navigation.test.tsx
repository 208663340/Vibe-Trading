import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import i18n from "@/i18n";
import { SidebarNavigation } from "../SidebarNavigation";
beforeAll(async () => { await i18n.changeLanguage("en"); });
it("exposes experiments, reports, comparison and connection settings", () => {
  render(<MemoryRouter><SidebarNavigation collapsed={false} /></MemoryRouter>);
  expect(screen.getAllByRole("link").map(link => link.getAttribute("href"))).toEqual(["/", "/reports", "/compare", "/settings"]);
  expect(screen.getByRole("link", { name: "Research workbench" })).toHaveAttribute("aria-current", "page");
});
it("keeps the compact menu keyboard accessible", () => {
  render(<MemoryRouter initialEntries={["/compare"]}><SidebarNavigation collapsed /></MemoryRouter>);
  const trigger = screen.getByRole("button", { name: "Browse features" });
  fireEvent.click(trigger);
  expect(within(screen.getByRole("navigation", { name: "Browse features" })).getAllByRole("link")).toHaveLength(4);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("navigation", { name: "Browse features" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
