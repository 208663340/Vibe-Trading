import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Compare } from "../Compare";
import type { RunData } from "@/lib/api";
import { pickSelectOption } from "@/tests/select";

const apiMock = vi.hoisted(() => ({
  listRuns: vi.fn(),
  getRun: vi.fn(),
}));
const toastMock = vi.hoisted(() => ({ error: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("sonner", () => ({ toast: toastMock }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const runs = [
  { run_id: "right", prompt: "Right", status: "success" },
  { run_id: "old", prompt: "Old", status: "success" },
  { run_id: "new", prompt: "New", status: "success" },
];

describe("Compare page", () => {
  beforeEach(() => {
    apiMock.listRuns.mockReset();
    apiMock.getRun.mockReset();
    toastMock.error.mockReset();
    apiMock.listRuns.mockResolvedValue(runs);
  });

  it("keeps the newest result when an older request resolves later", async () => {
    const oldRequest = deferred<RunData>();
    const newRequest = deferred<RunData>();
    apiMock.getRun.mockImplementation((runId: string) => {
      if (runId === "old") return oldRequest.promise;
      if (runId === "new") return newRequest.promise;
      return Promise.resolve({ status: "success", run_id: runId });
    });

    render(<MemoryRouter><Compare /></MemoryRouter>);
    const selectors = await screen.findAllByRole("combobox");
    await waitFor(() => expect(selectors[0]).toHaveTextContent("Old (success)"));
    await pickSelectOption(selectors[0], "New (success)");

    await act(async () => {
      newRequest.resolve({ status: "success", run_id: "new", metrics: { total_return: 0.3 } });
      await newRequest.promise;
    });
    expect(await screen.findByText("30.00%")).toBeInTheDocument();

    await act(async () => {
      oldRequest.resolve({ status: "success", run_id: "old", metrics: { total_return: 0.1 } });
      await oldRequest.promise;
    });

    expect(screen.getByText("30.00%")).toBeInTheDocument();
    expect(screen.queryByText("10.00%")).not.toBeInTheDocument();
  });

  it("ignores an older error and keeps loading the current selection", async () => {
    const oldRequest = deferred<RunData>();
    const newRequest = deferred<RunData>();
    apiMock.getRun.mockImplementation((runId: string) => {
      if (runId === "old") return oldRequest.promise;
      if (runId === "new") return newRequest.promise;
      return Promise.resolve({ status: "success", run_id: runId });
    });

    const { container } = render(<MemoryRouter><Compare /></MemoryRouter>);
    const selectors = await screen.findAllByRole("combobox");
    await waitFor(() => expect(selectors[0]).toHaveTextContent("Old (success)"));
    await pickSelectOption(selectors[0], "New (success)");

    await act(async () => {
      oldRequest.reject(new Error("old request failed"));
      await oldRequest.promise.catch(() => undefined);
    });

    expect(toastMock.error).not.toHaveBeenCalled();
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
    expect(screen.queryByText("Select two runs to compare their metrics.")).not.toBeInTheDocument();

    await act(async () => {
      newRequest.resolve({ status: "success", run_id: "new", metrics: { total_return: 0.2 } });
      await newRequest.promise;
    });
    expect(await screen.findByText("20.00%")).toBeInTheDocument();
  });

  it("localizes the title and gives metric deltas non-color verdicts", async () => {
    apiMock.getRun.mockImplementation((runId: string) => Promise.resolve({
      status: "success",
      run_id: runId,
      metrics: runId === "right"
        ? { total_return: 0.2, volatility: 0.2 }
        : { total_return: 0.1, volatility: 0.1 },
    }));

    render(<MemoryRouter><Compare /></MemoryRouter>);

    expect(screen.getByRole("heading", { level: 1, name: "Strategy Comparison" })).toHaveClass("text-2xl", "font-semibold");
    expect(await screen.findByText("Better:")).toHaveClass("sr-only");
    expect(screen.getByText("Worse:")).toHaveClass("sr-only");
    expect(screen.getByText("\u2191")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("\u2193")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("table").parentElement).toHaveClass("overflow-x-auto");
  });
});

it("honors the experiment pair supplied by the workbench", async () => {
  apiMock.listRuns.mockResolvedValue(runs);
  apiMock.getRun.mockResolvedValue({status: "success"});
  render(<MemoryRouter initialEntries={["/compare?left=new&right=old"]}><Compare /></MemoryRouter>);
  const selectors = await screen.findAllByRole("combobox");
  await waitFor(() => { expect(selectors[0]).toHaveTextContent("New (success)"); expect(selectors[1]).toHaveTextContent("Old (success)"); });
  fireEvent.click(screen.getByRole("button", { name: "Swap" }));
  expect(selectors[0]).toHaveTextContent("Old (success)");
  expect(selectors[1]).toHaveTextContent("New (success)");
  await pickSelectOption(selectors[0], "-- Select --");
  expect(selectors[0]).toHaveTextContent("-- Select --");
  expect(apiMock.getRun).not.toHaveBeenCalledWith("none");
});

it.each([[-0.2, -0.1], [0.2, 0.1], [-0.2, 0.1]])("treats a smaller drawdown magnitude as better (%s → %s)", async (baseline, comparison) => {
  apiMock.listRuns.mockResolvedValue(runs);
  apiMock.getRun.mockImplementation((id: string) => Promise.resolve({status:"success",run_id:id,metrics:{max_drawdown: id === "right" ? comparison : baseline}}));
  render(<MemoryRouter><Compare /></MemoryRouter>);
  expect(await screen.findByText("Better:")).toBeInTheDocument();
  expect(screen.queryByText("Worse:")).not.toBeInTheDocument();
  expect(screen.getByText("20.00%")).toBeInTheDocument();
  expect(screen.getByText("10.00%")).toBeInTheDocument();
  expect(screen.getByText("-10.00%")).toBeInTheDocument();
});
