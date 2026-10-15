import { Suspense, lazy, type ComponentType } from "react";
import { createBrowserRouter } from "react-router";
import {
  loadAgent,
  loadAlphaZoo,
  loadCompare,
  loadCorrelation,
  loadHome,
  loadOptionsLab,
  loadPortfolio,
  loadReports,
  loadRunDetail,
  loadRuntime,
  loadScheduled,
  loadSettings,
} from "@/app/chunks";
import { Layout } from "@/components/layout/Layout";

const Home = lazy(loadHome);
const Agent = lazy(loadAgent);
const RunDetail = lazy(loadRunDetail);
const Compare = lazy(loadCompare);
const Settings = lazy(loadSettings);
const Runtime = lazy(loadRuntime);
const Scheduled = lazy(loadScheduled);
const Reports = lazy(loadReports);
const Portfolio = lazy(loadPortfolio);
const Correlation = lazy(loadCorrelation);
const AlphaZoo = lazy(loadAlphaZoo);
const OptionsLab = lazy(loadOptionsLab);

function PageLoader() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: wrap(Agent) },
      { path: "/about", element: wrap(Home) },
      { path: "/agent", element: wrap(Agent) },
      { path: "/runtime", element: wrap(Runtime) },
      { path: "/scheduled", element: wrap(Scheduled) },
      { path: "/reports", element: wrap(Reports) },
      { path: "/portfolio", element: wrap(Portfolio) },
      { path: "/settings", element: wrap(Settings) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/options", element: wrap(OptionsLab) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
    ],
  },
]);
