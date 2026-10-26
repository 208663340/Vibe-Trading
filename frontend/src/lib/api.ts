import { authHeaders, withAuthTicket } from "@/lib/apiAuth";
import type {
  OptionsChainResponse,
  OptionsPayoffRequest,
  OptionsPayoffResponse,
} from "@/features/options/model";
import {
  ApiError,
  AUTH_REQUIRED_MESSAGE,
  isAuthRequiredError,
  METADATA_TIMEOUT_MS,
  performRequest,
  request,
} from "@/shared/http/client";

export { ApiError, AUTH_REQUIRED_MESSAGE, isAuthRequiredError };

const BASE = "";

export interface CorrelationResponse {
  labels: string[];
  matrix: number[][];
}

export interface CorrelationAnalysisResponse {
  correlation: CorrelationResponse | null;
  regime: CorrelationRegimeResponse | null;
  errors: { correlation?: string; regime?: string };
  coverage: { requested: string[]; missing: string[]; observations: number; first_date: string | null; last_date: string | null;
    diagnostics: Record<string, { symbol: string; market: string; source: string | null; attempts: Array<{ source: string; reason: "source_unavailable" | "fetch_failed" | "no_data" }> }> };
}

export interface RegimeEpisode {
  start: string;
  end: string | null;
}

export interface CorrelationRegimeResponse {
  labels: string[];
  dates: string[];
  density: (number | null)[];
  smoothed: (number | null)[];
  fused: number[];
  episodes: RegimeEpisode[];
  params: {
    days: number;
    corr_window: number;
    edge_threshold: number;
    smooth_window: number;
    enter_threshold: number;
    exit_threshold: number;
  };
}

export interface PortfolioPosition {
  source_id?: string;
  profile_id?: string;
  source_label?: string;
  broker: string;
  symbol: string;
  name: string;
  asset_type: string;
  market: string;
  currency: string;
  quantity: number;
  cost_price?: number | null;
  market_price?: number | null;
  market_value_usd: number;
  market_value_cny: number;
  unrealized_pnl_usd?: number | null;
  priced: boolean;
  updated_at: string;
  pricing_basis?: string;
  price_error?: string;
}

export interface PortfolioConnectorCompatibility {
  level: "native" | "contract_tested" | "experimental";
  contract_version: number;
  asset_scope: string;
  note: string;
}

/**
 * One portfolio source as of the last refresh.
 *
 * A source that could not be read has `status === "error"`, no totals, and
 * contributes nothing to the snapshot totals; `last_success_at` (when present)
 * is the timestamp of the last read that did succeed, and is only ever shown
 * as history — never as a current valuation.
 */
export interface PortfolioAccount {
  source_id?: string;
  profile_id?: string;
  label?: string;
  broker: string;
  status: "ok" | "error";
  last_success_at?: string;
  total_usd?: number | null;
  total_cny?: number | null;
  total_display?: number | null;
  priced_value_usd?: number;
  cash_usd?: number;
  unpriced_or_other_usd?: number;
  position_count?: number;
  priced_position_count?: number;
  unpriced_position_count?: number;
  error_code?: string;
  error?: string;
  failure_kind?: "authorization" | "transient";
  reconnect_required?: boolean;
  auth?: {
    method: string;
    renewal: "automatic" | "session" | "provider_managed";
    readonly: boolean;
    detail: string;
  };
  portfolio_compatibility?: PortfolioConnectorCompatibility;
}

export interface PortfolioSnapshot {
  snapshot_id: string;
  created_at: string;
  /** False whenever any enabled source did not reach `status === "ok"`. */
  complete: boolean;
  display_currency?: string;
  totals: { usd: number; cny: number; display?: number };
  valuation?: {
    priced_usd: number;
    cash_usd: number;
    unpriced_or_other_usd: number;
    identified_coverage: number;
  };
  fx: { usd_cny: number; usd_hkd: number; rates?: Record<string, number>; fetched_at: string; stale: boolean };
  accounts: PortfolioAccount[];
  positions: PortfolioPosition[];
  combined_holdings?: Array<{
    symbol: string;
    market_value_usd: number;
    asset_type?: string;
    brokers?: string[];
    unrealized_pnl_usd?: number;
  }>;
  /** Backend-authored English notes; rendered verbatim, not translated. */
  warnings: string[];
}

export interface PortfolioHistoryPoint {
  id: string;
  created_at: string;
  complete: number;
  total_usd: string;
  total_cny: string;
}

export interface PortfolioRefreshState {
  running: boolean;
  current: string | null;
  sources?: Record<string, { status: "idle" | "pending" | "refreshing" | "ok" | "error"; error?: string | null }>;
  brokers?: Record<string, { status: "idle" | "pending" | "refreshing" | "ok" | "error"; error?: string | null }>;
}

export interface PortfolioReconnectState {
  running: boolean;
  source_id: string | null;
  status: "idle" | "authorizing" | "authorized" | "error" | "timeout";
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface PortfolioSourceSettings {
  connection_id: string;
  label: string;
  enabled: boolean;
  order: number;
  include_cash: boolean;
}

export interface PortfolioSettings {
  display_currency: string;
  sources: PortfolioSourceSettings[];
}

export interface PortfolioSourceCatalogItem {
  id: string;
  connection_id: string;
  profile_id: string;
  connector: string;
  label: string;
  environment: "paper" | "live";
  transport: "local_tws" | "remote_mcp" | "broker_sdk" | "local_plugin";
  capabilities: string[];
  readonly: boolean;
  notes: string;
  selected: boolean;
  source_id?: string | null;
  supports_reconnect: boolean;
  credential_fields: CredentialField[];
  credential_status: Record<string, boolean>;
  credentials_configured: boolean;
  portfolio_compatibility: PortfolioConnectorCompatibility;
}

export interface CredentialField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
}

export interface ConnectorOnboarding {
  schema_version: number;
  auth_type: string;
  credential_fields: CredentialField[];
  dependency: string | null;
  install_command: string | null;
  test_operation: string;
  setup_hint: string;
  secret_storage: "os_keyring" | null;
}

export interface LocalConnection {
  id: string;
  profile_id: string;
  label: string;
  /** Broker account the reads are scoped to; empty until the user selects one. */
  account_ref: string;
  /** True when this connection reads nothing until an account is selected. */
  account_selection_required: boolean;
  connector: string;
  environment: "paper" | "live";
  transport: "local_tws" | "remote_mcp" | "broker_sdk" | "local_plugin";
  readonly: boolean;
  capabilities: string[];
  supports_reconnect: boolean;
  credential_fields: CredentialField[];
  credential_status: Record<string, boolean>;
  credentials_configured: boolean;
  onboarding?: ConnectorOnboarding;
  portfolio_compatibility: PortfolioConnectorCompatibility;
}

export interface ReadonlyConnectionProfile {
  id: string;
  connector: string;
  label: string;
  environment: "paper" | "live";
  transport: "local_tws" | "remote_mcp" | "broker_sdk" | "local_plugin";
  capabilities: string[];
  readonly: boolean;
  notes: string;
  local_plugin: boolean;
  credential_fields: CredentialField[];
  onboarding?: ConnectorOnboarding;
  supports_reconnect: boolean;
  account_selection_required: boolean;
  portfolio_compatibility: PortfolioConnectorCompatibility;
  invalid_plugin?: boolean;
  directory?: string;
  error?: string;
}

/** One account a broker login can reach, as the backend maps it for a picker. */
export interface BrokerAccountChoice {
  account_ref: string;
  label: string;
  is_default: boolean;
  agentic_allowed: boolean;
  deactivated: boolean;
}

export interface ConnectionsResponse {
  status: string;
  connections: LocalConnection[];
  profiles: ReadonlyConnectionProfile[];
  plugin_directory: string;
}

export interface PortfolioSettingsResponse {
  status: string;
  settings: PortfolioSettings;
  catalog: PortfolioSourceCatalogItem[];
}

export const api = {
  getCorrelationAnalysis: (codes: string, days: number, method: "pearson" | "spearman", includeRegime: boolean, signal?: AbortSignal) =>
    request<CorrelationAnalysisResponse>(`/correlation/analysis?${new URLSearchParams({ codes, days: String(days), method, include_regime: String(includeRegime) })}`, { signal }),
  getCorrelation: (codes: string, days: number, method: "pearson" | "spearman") =>
    request<CorrelationResponse>(
      `/correlation?codes=${encodeURIComponent(codes)}&days=${encodeURIComponent(String(days))}&method=${encodeURIComponent(method)}`,
    ),
  getCorrelationRegime: (codes: string, days: number) =>
    request<CorrelationRegimeResponse>(
      `/correlation/regime?codes=${encodeURIComponent(codes)}&days=${encodeURIComponent(String(days))}`,
    ),
  getPortfolio: () => request<{ status: string; snapshot: PortfolioSnapshot | null }>("/api/portfolio"),
  getPortfolioAnalysisContext: (snapshotId: string) =>
    request<{ status: string; context: Record<string, unknown> }>(`/api/portfolio/analysis-context?snapshot_id=${encodeURIComponent(snapshotId)}`),
  refreshPortfolio: () => request<{ status: string; snapshot: PortfolioSnapshot }>("/api/portfolio/refresh", { method: "POST" }),
  getPortfolioRefreshStatus: () => request<{ status: string; refresh: PortfolioRefreshState }>("/api/portfolio/refresh-status"),
  reconnectPortfolioSource: (sourceId: string) =>
    request<{ status: string; reconnect: PortfolioReconnectState }>(`/api/portfolio/sources/${encodeURIComponent(sourceId)}/reconnect`, { method: "POST" }),
  getPortfolioReconnectStatus: () =>
    request<{ status: string; reconnect: PortfolioReconnectState }>("/api/portfolio/reconnect-status"),
  getPortfolioSettings: () => request<PortfolioSettingsResponse>("/api/portfolio/settings"),
  updatePortfolioSettings: (settings: PortfolioSettings) =>
    request<PortfolioSettingsResponse>("/api/portfolio/settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getConnections: () => request<ConnectionsResponse>("/api/connections"),
  createConnection: (payload: { id: string; profile_id: string; label: string }) =>
    request<{ status: string; connection: LocalConnection }>("/api/connections", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveConnectionCredentials: (connectionId: string, values: Record<string, string>) =>
    request<{ status: string; credential_status: Record<string, boolean> }>(
      `/api/connections/${encodeURIComponent(connectionId)}/credentials`,
      { method: "POST", body: JSON.stringify({ values }) },
    ),
  checkConnection: (connectionId: string) =>
    request<{ status: string; connection_id: string; report: Record<string, unknown> }>(
      `/api/connections/${encodeURIComponent(connectionId)}/check`,
      { method: "POST" },
    ),
  getConnectionAccounts: (connectionId: string) =>
    request<{ status: string; connection_id: string; account_ref: string; accounts: BrokerAccountChoice[] }>(
      `/api/connections/${encodeURIComponent(connectionId)}/accounts`,
    ),
  selectConnectionAccount: (connectionId: string, accountRef: string) =>
    request<{ status: string; connection: LocalConnection }>(
      `/api/connections/${encodeURIComponent(connectionId)}/account`,
      { method: "PUT", body: JSON.stringify({ account_ref: accountRef }) },
    ),
  deleteConnection: (connectionId: string) =>
    request<{ status: string; deleted: string }>(
      `/api/connections/${encodeURIComponent(connectionId)}`,
      { method: "DELETE" },
    ),
  getPortfolioHistory: (limit = 180) =>
    request<{ status: string; history: PortfolioHistoryPoint[] }>(`/api/portfolio/history?limit=${encodeURIComponent(String(limit))}`),
  downloadPortfolioCsv: async () => {
    return performRequest("/api/portfolio/export.csv", { headers: authHeaders() }, (response) => response.blob());
  },
  listRuns: (limit?: number) => request<RunListItem[]>(`/runs${limit ? `?limit=${encodeURIComponent(String(limit))}` : ""}`),
  getRun: (id: string, params: RunDetailParams = {}, signal?: AbortSignal) => {
    const q = new URLSearchParams();
    if (params.chart_payload) q.set("chart_payload", params.chart_payload);
    if (params.chart_symbol) q.set("chart_symbol", params.chart_symbol);
    const qs = q.toString();
    return request<RunData>(`/runs/${id}${qs ? `?${qs}` : ""}`, { signal });
  },
  getRunCode: (id: string) => request<Record<string, string>>(`/runs/${id}/code`),
  getRunFactor: (id: string) => request<FactorReportPayload>(`/runs/${id}/factor`),
  getRunAttribution: (id: string) => request<AttributionResponse>(`/runs/${encodeURIComponent(id)}/attribution`),
  // Alpha Zoo API
  getAlphaReadiness: () => request<AlphaReadiness>("/alpha/readiness", {}, METADATA_TIMEOUT_MS),
  getAlphaBenchJob: (jobId: string) => request<AlphaJobSnapshot<AlphaBenchResult>>(`/alpha/bench/${encodeURIComponent(jobId)}`, {}, METADATA_TIMEOUT_MS),
  getAlphaCompareJob: (jobId: string) => request<AlphaJobSnapshot<AlphaCompareResult>>(`/alpha/compare/${encodeURIComponent(jobId)}`, {}, METADATA_TIMEOUT_MS),
  listAlphas: (params: AlphaListParams = {}) => {
    const q = new URLSearchParams();
    if (params.zoo) q.set("zoo", params.zoo);
    if (params.theme) q.set("theme", params.theme);
    if (params.universe) q.set("universe", params.universe);
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<AlphaListResponse>(`/alpha/list${qs ? `?${qs}` : ""}`);
  },
  getAlpha: (alphaId: string) =>
    request<AlphaDetailResponse>(`/alpha/${encodeURIComponent(alphaId)}`),
  createAlphaBench: (body: AlphaBenchRequest) =>
    request<{ status: string; job_id: string }>("/alpha/bench", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaBenchStreamUrl: (jobId: string, signal?: AbortSignal) =>
    withAuthTicket(`${BASE}/alpha/bench/${encodeURIComponent(jobId)}/stream`, signal),
  createAlphaCompare: (body: AlphaCompareRequest) =>
    request<{ status: string; job_id: string }>("/alpha/compare", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaCompareStreamUrl: (jobId: string, signal?: AbortSignal) =>
    withAuthTicket(`${BASE}/alpha/compare/${encodeURIComponent(jobId)}/stream`, signal),

  // Options Lab
  analyzeOptionsPayoff: (body: OptionsPayoffRequest, signal?: AbortSignal) =>
    request<OptionsPayoffResponse>("/options/payoff", {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  getOptionsChain: (ticker: string, expiration?: number) => {
    const q = new URLSearchParams();
    q.set("ticker", ticker);
    if (expiration !== undefined) q.set("expiration", String(expiration));
    return request<OptionsChainResponse>(`/options/chain?${q.toString()}`);
  },


};

export interface RunListItem {
  run_id: string;
  status: string;
  created_at: string;
  prompt?: string;
  total_return?: number;
  sharpe?: number;
  codes?: string[];
  start_date?: string;
  end_date?: string;
}

export interface RunDetailParams {
  chart_payload?: "summary";
  chart_symbol?: string;
}

export interface PriceBar {
  time: string;
  timestamp?: string;
  code?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TradeMarker {
  time: string;
  timestamp?: string;
  code?: string;
  side: "BUY" | "SELL";
  price: number;
  qty?: number;
  reason?: string;
  text?: string;
}

export interface EquityPoint {
  time: string;
  equity: string | number;
  drawdown: string | number;
}

/** Monte Carlo fan-chart payload: percentile envelope + sampled paths over trade order. */
export interface MonteCarloEquityPaths {
  steps: number[];
  initial_capital: number;
  actual: number[];
  band_p5: number[];
  band_p25: number[];
  band_p50: number[];
  band_p75: number[];
  band_p95: number[];
  samples: number[][];
}

export interface ValidationData {
  monte_carlo?: {
    actual_sharpe: number;
    actual_max_dd: number;
    p_value_sharpe: number;
    p_value_max_dd: number;
    simulated_sharpe_mean: number;
    simulated_sharpe_std: number;
    simulated_sharpe_p5: number;
    simulated_sharpe_p95: number;
    n_simulations: number;
    n_trades: number;
    sharpe_samples?: number[];
    equity_paths?: MonteCarloEquityPaths;
    error?: string;
  };
  bootstrap?: {
    observed_sharpe: number;
    ci_lower: number;
    ci_upper: number;
    median_sharpe: number;
    prob_positive: number;
    confidence: number;
    n_bootstrap: number;
    sharpe_samples?: number[];
    error?: string;
  };
  walk_forward?: {
    n_windows: number;
    windows: Array<{
      window: number;
      start: string;
      end: string;
      return: number;
      sharpe: number;
      max_dd: number;
      trades: number;
      win_rate: number;
    }>;
    profitable_windows: number;
    consistency_rate: number;
    return_mean: number;
    return_std: number;
    sharpe_mean: number;
    sharpe_std: number;
    error?: string;
  };
}

export interface RiskXRayPayload {
  inputs?: {
    symbols?: string[];
    weights?: Record<string, number>;
    aligned_days?: number;
    return_observations?: number;
    first_date?: string;
    last_date?: string;
  };
  concentration?: { hhi?: number; effective_n?: number; top_weight?: number };
  volatility?: { annualized_vol?: number };
  drawdown?: { max_drawdown?: number };
  tail_risk?: Record<string, unknown>;
  diversification?: Record<string, unknown>;
  correlation?: Record<string, unknown>;
  skipped?: string[];
  warnings?: string[];
}

export interface RebalanceNotesPayload {
  rebalances?: Array<{
    date: string;
    turnover: number;
    entries?: Array<{ code: string; to: number }>;
    exits?: Array<{ code: string; from: number }>;
    top_moves?: Array<{ code: string; from: number; to: number; delta: number }>;
  }>;
  summary?: {
    target_change_count: number;
    /** Legacy key on runs produced before the #1275 execution-evidence fix. */
    rebalance_count?: number;
    turnover_total: number;
    turnover_mean: number;
    turnover_max: number;
    largest_rebalance_date?: string | null;
    rebalance_executed_bars?: number;
    rebalance_executed_fills?: number;
    rebalance_realized_turnover?: number;
  };
}

export interface FactorIcStats {
  ic_mean?: number | null;
  ic_std?: number | null;
  ir?: number | null;
  ic_positive_ratio?: number | null;
  ic_count?: number | null;
  [key: string]: unknown;
}

export interface FactorResult {
  name: string;
  path: string;
  ic_series: Array<{ date: string; ic: number }>;
  ic_stats?: FactorIcStats;
  group_equity: Array<{ date: string } & Record<string, number | string>>;
  n_groups: number;
  long_short_spread: number | null;
  group_final_equity: Record<string, number>;
  truncated?: {
    ic_series?: boolean;
    ic_stats?: boolean;
    group_equity?: boolean;
  };
}

export interface FactorReportPayload {
  exists: boolean;
  factors: FactorResult[];
  ic_correlation: { labels: string[]; matrix: number[][] } | null;
}

// --- Attribution types (GET /runs/{runId}/attribution) ---

export interface AttributionBenchmarkInfo {
  ticker: string | null;
  mode: "auto_equal_weight" | "explicit";
}

export interface AttributionRollingPoint {
  date: string;
  beta: number;
  alpha_annualized: number;
}

export interface AttributionCumulativePoint {
  date: string;
  portfolio: number;
  benchmark: number;
  /** Portfolio-minus-benchmark cumulative return; plotted as the active line. */
  active: number;
}

export interface AttributionFactor {
  beta: number;
  alpha_per_period: number;
  alpha_annualized: number;
  alpha_t_stat: number;
  r_squared: number;
  n_obs: number;
  rolling_window: number;
  rolling: AttributionRollingPoint[] | null;
  cumulative: AttributionCumulativePoint[];
}

export interface AttributionSectorEntry {
  sector: string;
  portfolio_weight: number;
  benchmark_weight: number;
  portfolio_return: number;
  benchmark_return: number;
  allocation: number;
  selection: number;
  interaction: number;
  total: number;
}

export interface AttributionBrinson {
  mode: "symbol" | "asset_class" | "invested_cash";
  portfolio_return: number;
  benchmark_return: number;
  active_return: number;
  allocation: number;
  selection: number;
  interaction: number;
  sectors: AttributionSectorEntry[];
}

export interface AttributionResponse {
  exists: boolean;
  benchmark: AttributionBenchmarkInfo | null;
  factor: AttributionFactor | null;
  brinson: AttributionBrinson | null;
  notes: string[];
}

export interface RunData {
  status: string;
  run_id: string;
  prompt?: string;
  elapsed_seconds?: number;
  run_directory?: string;
  run_stage?: string;
  run_context?: Record<string, unknown>;

  metrics?: BacktestMetrics;
  artifacts?: ArtifactInfo[];
  run_card?: RunCard;
  risk_xray?: RiskXRayPayload;
  rebalance_notes?: RebalanceNotesPayload;
  validation?: ValidationData;
  has_factor_artifacts?: boolean;

  chart_symbols?: string[];
  price_series?: Record<string, PriceBar[]>;
  indicator_series?: Record<string, Record<string, IndicatorPoint[]>>;
  trade_markers?: TradeMarker[];
  equity_curve?: EquityPoint[];
  trade_log?: Array<Record<string, string>>;
  /** Full equity.csv rows (timestamp/equity/drawdown as strings); not capped like equity_curve. */
  artifacts_equity_csv?: Array<Record<string, string>>;
  artifacts_metrics_csv?: Array<Record<string, string>>;
  artifacts_trades_csv?: Array<Record<string, string>>;
  /** Filled portfolio weights per date (rows: {timestamp, <symbol>: weight-string}). */
  artifacts_positions_csv?: Array<Record<string, string>>;
  /** Requested target weights per date, same row shape as artifacts_positions_csv. */
  artifacts_target_positions_csv?: Array<Record<string, string>>;
  run_logs?: Array<{ source?: string; line_number?: number; message?: string }>;
}

// --- Positions sector-map types (GET /runs/{runId}/positions/sectors) ---

/** Asset classes reported by the backend sector-map endpoint. */
export type SectorAssetClass =
  | "a_share"
  | "us_equity"
  | "hk_equity"
  | "india_equity"
  | "kr_equity"
  | "ca_equity"
  | "ar_equity"
  | "br_equity"
  | "uk_equity"
  | "vietnam_equity"
  | "index"
  | "crypto"
  | "futures"
  | "forex";

export interface SectorInfo {
  asset_class: SectorAssetClass;
  /** Resolved industry name, or null when unresolved / not applicable. */
  industry: string | null;
  /** Provenance of `industry` (e.g. "eastmoney"), null when unresolved. */
  industry_source: string | null;
}

export interface SectorMapResponse {
  ok: boolean;
  run_id?: string;
  resolved_at?: string;
  cached?: boolean;
  symbols: Record<string, SectorInfo>;
  unresolved?: string[];
  /** Total symbol columns in positions.csv (may exceed `symbol_limit`). */
  total_symbols?: number;
  /** Max symbols that receive a network industry lookup per resolve. */
  symbol_limit?: number;
  /** Present when the run has no positions artifact. */
  note?: string;
}

/**
 * Fetch the resolved sector map for one run's positions artifact.
 * Uses the same auth-header + error-envelope conventions as every other
 * fetcher in this module (via the shared `request` helper).
 */
export function fetchRunSectorMap(runId: string, refresh?: boolean): Promise<SectorMapResponse> {
  const qs = refresh ? "?refresh=1" : "";
  return request<SectorMapResponse>(`/runs/${encodeURIComponent(runId)}/positions/sectors${qs}`);
}

export interface RunCardToolTrace {
  tool: "backtest" | "load_data" | "generate_signals";
  started_at: string;
  ended_at: string;
  status: "ok" | "error" | "cancelled";
  args_hash: string;
  result_hash: string;
}

export interface RunCardCitation {
  metric: string;
  artifact_id: string;
  column: string;
  /** One-based data row, excluding the CSV header. */
  row: number;
  sha256: string;
}

export interface RunCard {
  schema_version?: string;
  generated_at?: string;
  run_dir?: string;
  backtest?: Record<string, unknown>;
  reproducibility?: Record<string, unknown>;
  data_sources?: string[];
  metrics?: Record<string, unknown>;
  // Carried alongside `metrics`, which is scalar-only: dict/list-shaped metrics
  // (e.g. which sleeve a plan rejection dropped) reach the card here.
  structured_metrics?: Record<string, unknown>;
  validation?: unknown;
  warnings?: string[];
  artifacts?: RunCardArtifact[];
  tool_traces?: RunCardToolTrace[];
  citations?: RunCardCitation[];
  [key: string]: unknown;
}

export interface RunCardArtifact {
  path: string;
  size_bytes: number;
  sha256: string;
}

export interface BacktestMetrics {
  final_value: number;
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  win_rate: number;
  trade_count: number;
  [key: string]: number;
}


export interface IndicatorPoint {
  time: string;
  value: number;
}

export interface ArtifactInfo {
  name: string;
  path: string;
  type: string;
  size: number;
  exists: boolean;
}

export interface AlphaListParams {
  zoo?: string;
  theme?: string;
  universe?: string;
  limit?: number;
}

export interface AlphaSummary {
  id: string;
  zoo: string;
  theme: string[];
  universe: string[];
  nickname?: string;
  decay_horizon?: number | null;
  min_warmup_bars?: number | null;
  requires_sector?: boolean;
}

export interface AlphaListResponse {
  status: string;
  alphas: AlphaSummary[];
  total: number;
  returned: number;
  truncated: boolean;
}

export interface AlphaDetail {
  id: string;
  zoo: string;
  module_path?: string;
  meta: Record<string, unknown>;
}

export interface AlphaDetailResponse {
  status: string;
  alpha: AlphaDetail;
  source_code: string;
}

export interface AlphaBenchRequest {
  request_id?: string;
  zoo: string;
  universe: string;
  period: string;
  top?: number;
  alpha_id?: string;
}

export interface AlphaReadiness {
  universes: Record<string, { ready: boolean; reason: "public_data" | "tushare_ready" | "tushare_token_missing" | "tushare_dependency_missing" | "single_asset" }>;
  zoo_counts: Record<string, number>;
}

export interface AlphaProgress { n_done: number; n_total: number; current_alpha_id?: string; stage?: "loading_data" | "preparing_returns" | "computing" }
export interface AlphaJobSnapshot<T> { job_id: string; status: "queued" | "running" | "done" | "error"; progress: AlphaProgress; result: T | null; error: string | null }

export interface AlphaBenchTopRow {
  id: string;
  ic_mean: number;
  ir: number;
  theme: string[];
  formula_latex: string;
  category: "alive" | "reversed" | "dead";
}

export interface AlphaDataMeta {
  fetched_instruments?: number;
  requested_instruments?: number;
  missing_instruments?: string[];
  survivorship_bias?: boolean;
  degraded?: boolean;
}

export interface AlphaBenchResult {
  n_alphas_tested?: number;
  meta?: AlphaDataMeta;
  alive: number;
  reversed: number;
  dead: number;
  skipped?: number;
  top5_by_ir: AlphaBenchTopRow[];
  dead_examples: AlphaBenchTopRow[];
  by_theme: Record<string, { alive: number; reversed: number; dead: number }>;
}

export interface AlphaCompareRequest {
  request_id?: string;
  alpha_ids: string[];
  universe: string;
  period: string;
  /** One of: ir | ic_mean | ic_positive_ratio | ic_count (default ir). */
  sort?: string;
}

export interface AlphaCompareRow {
  rank: number;
  id: string;
  zoo: string;
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_positive_ratio: number;
  ic_count: number;
  /** `delta_<sort>_vs_best` — gap to the top-ranked alpha on the active metric. */
  [deltaKey: string]: number | string;
}

export interface AlphaCompareSkip {
  id: string;
  reason: string;
}

export interface AlphaCompareResult {
  meta?: AlphaDataMeta;
  universe: string;
  period: string;
  sort: string;
  n_compared: number;
  n_skipped: number;
  winner: string;
  ranking: AlphaCompareRow[];
  skipped: AlphaCompareSkip[];
}
