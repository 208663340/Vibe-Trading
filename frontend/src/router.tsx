import { Suspense, lazy, type ComponentType } from "react";
import { createBrowserRouter, Navigate } from "react-router";
import { loadWorkbench, loadCompare, loadReports, loadRunDetail, loadSettings } from "@/app/chunks";
import { Layout } from "@/components/layout/Layout";
const Workbench = lazy(loadWorkbench), Compare = lazy(loadCompare), Reports = lazy(loadReports), RunDetail = lazy(loadRunDetail), Settings = lazy(loadSettings);
function wrap(Component: ComponentType) { return <Suspense fallback={<div className="p-8 text-muted-foreground" role="status">Loading…</div>}><Component /></Suspense>; }
export const router = createBrowserRouter([{ element: <Layout />, children: [
  { path: "/", element: wrap(Workbench) },
  { path: "/agent", element: <Navigate to="/" replace /> },
  { path: "/about", element: <Navigate to="/" replace /> },
  { path: "/reports", element: wrap(Reports) },
  { path: "/runs/:runId", element: wrap(RunDetail) },
  { path: "/compare", element: wrap(Compare) },
  { path: "/settings", element: wrap(Settings) },
]}]);
