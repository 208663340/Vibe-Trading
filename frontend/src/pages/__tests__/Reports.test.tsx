import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Reports } from "../Reports";
import { pickSelectOption } from "@/tests/select";

const apiMock = vi.hoisted(() => ({
  listRuns: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMock,
}));

describe("Reports page", () => {
  beforeEach(() => {
    apiMock.listRuns.mockReset();
  });

  it("gives a first-time user an actionable path to create a backtest", async () => {
    apiMock.listRuns.mockResolvedValue([]);
    render(<Reports />, { wrapper: MemoryRouter });
    expect(await screen.findByText("No reports yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open workbench" })).toHaveAttribute("href", "/");
  });

  it("lists backtest reports newest first with Full Report links and skips non-report runs", async () => {
    apiMock.listRuns.mockResolvedValue([
      {
        run_id: "old-report",
        status: "success",
        created_at: "2026-06-01T00:00:00Z",
        prompt: "Old report",
        codes: ["MSFT"],
        total_return: 0.05,
        sharpe: 1.1,
      },
      {
        run_id: "chat-only",
        status: "success",
        created_at: "2026-06-03T00:00:00Z",
        prompt: "No metrics",
        codes: [],
      },
      {
        run_id: "new-report",
        status: "success",
        created_at: "2026-06-04T00:00:00Z",
        prompt: "New report",
        codes: ["AAPL"],
        total_return: 0.12,
        sharpe: 1.8,
      },
    ]);

    render(<Reports />, { wrapper: MemoryRouter });

    expect(await screen.findByText("Backtest Report Library")).toBeInTheDocument();
    expect(apiMock.listRuns).toHaveBeenCalledWith(100);
    expect(screen.queryByText("chat-only")).not.toBeInTheDocument();
    const reportRunLinks = screen.getAllByRole("link", { name: /-report$/ });
    expect(reportRunLinks[0]).toHaveAttribute("href", "/runs/new-report");
    expect(reportRunLinks[1]).toHaveAttribute("href", "/runs/old-report");
    const fullReportLinks = screen.getAllByRole("link", { name: "Full Report" });
    expect(fullReportLinks[0]).toHaveAttribute("href", "/runs/new-report");
    expect(fullReportLinks[1]).toHaveAttribute("href", "/runs/old-report");
    expect(screen.getByRole("textbox", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Status" })).toBeInTheDocument();
  });

  it("filters reports by search text", async () => {
    apiMock.listRuns.mockResolvedValue([
      {
        run_id: "aapl-report",
        status: "success",
        created_at: "2026-06-04T00:00:00Z",
        prompt: "Apple strategy",
        codes: ["AAPL"],
        total_return: 0.12,
      },
      {
        run_id: "msft-report",
        status: "success",
        created_at: "2026-06-03T00:00:00Z",
        prompt: "Microsoft strategy",
        codes: ["MSFT"],
        total_return: 0.08,
      },
    ]);

    render(<Reports />, { wrapper: MemoryRouter });
    await screen.findByText("aapl-report");

    fireEvent.change(screen.getByRole("textbox", { name: "Search" }), {
      target: { value: "MSFT" },
    });

    expect(screen.queryByText("aapl-report")).not.toBeInTheDocument();
    expect(screen.getByText("msft-report")).toBeInTheDocument();
  });

  it("sorts and filters reports using the keyboard pickers", async () => {
    apiMock.listRuns.mockResolvedValue([
      { run_id: "old-report", status: "success", created_at: "2026-06-01T00:00:00Z", total_return: 0.2 },
      { run_id: "new-report", status: "failed", created_at: "2026-06-02T00:00:00Z", total_return: 0.1 },
    ]);
    render(<Reports />, { wrapper: MemoryRouter });
    await screen.findByRole("link", { name: "new-report" });
    expect(screen.getAllByRole("link", { name: /-report$/ })[0]).toHaveTextContent("new-report");

    await pickSelectOption(screen.getByRole("combobox", { name: "Sort" }), "Return high to low");
    expect(screen.getAllByRole("link", { name: /-report$/ })[0]).toHaveTextContent("old-report");
    await pickSelectOption(screen.getByRole("combobox", { name: "Status" }), "failed");
    expect(screen.queryByRole("link", { name: "old-report" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "new-report" })).toBeVisible();
    await pickSelectOption(screen.getByRole("combobox", { name: "Status" }), "All statuses");
    expect(screen.getAllByRole("link", { name: /-report$/ })[0]).toHaveTextContent("old-report");
  });
});
