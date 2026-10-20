export type ApiKeyRecord = {
  id: number;
  org_id: number;
  name: string;
  prefix: string;
  scopes: string;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export type ApiKeyCreateResponse = {
  id: number;
  org_id: number;
  name: string;
  prefix: string;
  plaintext: string;
};

export type Account = {
  email: string;
  org_id: number;
  organization: string;
  plan: string;
  created_at: string;
  real_mode_enabled?: boolean;
  real_mode_approved_at?: string | null;
  real_mode_daily_limit_usd?: number;
  bot_consecutive_failures?: number;
  provider_credentials?: {
    gemini?: ProviderCredentialSummary;
  };
};

export type ProviderCredentialSummary = {
  configured: boolean;
  provider: "gemini";
  key_hint?: string;
  model?: string;
  created_at?: string;
  updated_at?: string;
};

export type Dashboard = {
  email: string;
  org_id: number;
  plan: string;
  requests_this_month: number;
  simulated_volume_usd: number;
  active_api_keys: number;
  total_simulated_trades?: number;
  total_volume_usd?: number;
  total_fees_usd?: number;
  realized_pnl_usd?: number;
  unrealized_pnl_usd?: number;
  last_30_days_chart_points?: Array<{ date: string; trades: number; volume_usd: number }>;
};

export type Usage = {
  requests_today: number;
  requests_month: number;
  plan: string;
  quota: {
    simulate_order: number;
    token_audit: number;
    active_api_keys: number;
  };
};

export type SimulationResult = {
  status: string;
  trade_id: number | null;
  token_address: string;
  side: "BUY" | "SELL";
  input_amount: number;
  executed_price: number;
  fees_paid: number;
  slippage_paid: number;
  token_tax_paid: number;
  net_amount: number;
  estimated_quantity: number;
  realized_pnl_usd: number | null;
  source: string;
  warning: string;
};

export type CopilotProposal = {
  id: number;
  org_id: number;
  token_address: string;
  chain: string;
  symbol: string | null;
  side: "BUY" | "SELL";
  amount_usd: number;
  score: number;
  rationale: string;
  mode: "paper" | "real";
  status: string;
  execution_payload: SimulationResult | Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  approved_at: string | null;
};

export type TokenDiscoveryHistoryRecord = {
  id: number;
  org_id: number;
  token_address: string;
  chain: string;
  symbol: string | null;
  score: number | null;
  recommendation: string | null;
  rationale: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type CopilotScanResponse = {
  org_id: number;
  execution_mode: "paper_auto" | "copilot" | "real_approval";
  scanner_run: {
    id: number;
    org_id: number;
    query: string;
    execution_mode: "paper_auto" | "copilot" | "real_approval";
    status: string;
    thresholds: Record<string, unknown>;
    candidate_count: number;
    blocked_count: number;
    queued_count: number;
    paper_executed_count: number;
    provider_readiness_summary: Record<string, unknown>;
    error_summary: string | null;
    created_at: string;
    completed_at: string | null;
  };
  created: CopilotProposal[];
  skipped: Array<{ token_address: string; reason: string; score: number }>;
};

export type AuditEvent = {
  id: number;
  org_id: number | null;
  actor_id: number | null;
  actor_type: string;
  action: string;
  target_type: string;
  target_id: string | null;
  ip: string | null;
  ua: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
};

export type Health = {
  ok: boolean;
  app: string;
  mode: string;
  orderSubmissionEnabled: boolean;
  wallet_provider?: string;
  wallet_set_id?: string | null;
  wallets_loaded?: boolean;
  settlement_auth_mode?: "manual" | "programmatic";
  autonomous_payments_enabled?: boolean;
  providers?: Record<string, { status: string; latency_ms: number | null; last_error: string | null }>;
};

export type BillableArtifact = {
  id: string;
  name: string;
  description: string;
  price_usd: number;
  type: "delivery_packet" | "review_bundle" | "market_intel_brief";
};

export type PaymentIntentRecord = {
  payment_id: string;
  artifact_id: string;
  artifact_name: string;
  artifact_type: string;
  amount_usd: number;
  buyer_address: string;
  deposit_address: string;
  job_id: string | null;
  status: "pending" | "verified" | "failed";
  settlement_status: string | null;
  reason: string | null;
  tx_hash: string | null;
  circle_transaction_id: string | null;
  executed: boolean;
  download_url: string | null;
  execution_message: string | null;
  created_at: string;
  updated_at: string;
};

export type PaymentIntentResponse = {
  payment_id: string;
  amount_usd: number;
  currency: "USDC";
  deposit_address: string;
  payment_requirement: Record<string, unknown>;
  status: "pending" | "verified" | "failed";
  job_id: string | null;
};

export type PaymentVerificationResponse = {
  payment_id: string;
  status: "pending" | "verified" | "failed";
  unlocked_artifact_id: string | null;
  settlement_status: string | null;
  reason: string | null;
};

export type JobExecutionResponse = {
  artifact_id: string;
  status: "completed" | "failed";
  message: string;
  download_url: string | null;
};

export type NexusJob = {
  id: string;
  description: string;
  state: "REQUESTED" | "PAYMENT_UNLOCKED" | "WORK_RESERVED" | "REVIEW_PENDING" | "DELIVERED";
  payment_id: string | null;
  artifact_id: string | null;
  artifact_type: string | null;
  amount_usdc: number | null;
  buyer_address: string | null;
  settlement_status: string | null;
  tx_hash: string | null;
  download_url: string | null;
  history: Array<{ state: string; reason: string; at: string }>;
};

export type ArcJobProof = {
  job_id: string;
  task_id: string;
  agent_id: string;
  status: string;
  proof_hash: string;
  timestamp: string;
  metadata: Record<string, string>;
  work_unit_id: string | null;
  payment_id: string | null;
  payment_status: string | null;
  settlement_status: string | null;
  tx_hash: string | null;
  onchain_write_status: string;
};

export type AgentChatTurn = {
  role: "user" | "assistant";
  text: string;
};

export type AgentToolEvent = {
  id?: string | null;
  name: string;
  args: Record<string, unknown>;
  response?: Record<string, unknown> | null;
};

export type AgentChatResponse = {
  reply: string;
  events: AgentToolEvent[];
  orders: PaymentIntentRecord[];
  settlement_auth_mode: "manual" | "programmatic";
  autonomous_payments_enabled: boolean;
  gemini?: {
    configured: boolean;
    source: "organization" | "global" | "vertexai" | "unconfigured";
    model: string;
    key_hint?: string | null;
    vertex_project_configured?: boolean;
    vertex_location?: string | null;
  } | null;
};

const configuredBase = import.meta.env.VITE_TTM_API_BASE_URL || (typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8010");
export const API_ROOT = configuredBase.replace(/\/api\/v1\/?$/, "").replace(/\/$/, "");
export const API_BASE = `${API_ROOT}/api/v1`;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path.startsWith("http") ? path : `${API_BASE}${path}`, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // keep HTTP status detail
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function bearer(token: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function apiKeyHeader(apiKey: string | null): HeadersInit {
  return apiKey ? { "X-API-Key": apiKey } : {};
}

export const api = {
  signup(email: string, password: string) {
    return request<{ id: number; email: string; org_id: number; plan: string }>("/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  },
  async login(email: string, password: string) {
    const body = new URLSearchParams({ username: email, password });
    return request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
  },
  account(token: string) {
    return request<Account>("/saas/account", { headers: bearer(token) });
  },
  dashboard(token: string) {
    return request<Dashboard>("/saas/dashboard", { headers: bearer(token) });
  },
  usage(token: string) {
    return request<Usage>("/saas/usage", { headers: bearer(token) });
  },
  apiKeys(token: string) {
    return request<ApiKeyRecord[]>("/saas/api-keys", { headers: bearer(token) });
  },
  createApiKey(token: string, name: string) {
    return request<ApiKeyCreateResponse>("/saas/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify({ name }),
    });
  },
  revokeApiKey(token: string, id: number) {
    return request<{ id: number; revoked_at: string }>(`/saas/api-keys/${id}`, {
      method: "DELETE",
      headers: bearer(token),
    });
  },
  saveGeminiCredential(token: string, apiKey: string, model: string) {
    return request<{ org_id: number; credential: ProviderCredentialSummary }>(
      "/saas/provider-credentials/gemini",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...bearer(token) },
        body: JSON.stringify({ api_key: apiKey, model }),
      },
    );
  },
  deleteGeminiCredential(token: string) {
    return request<{ org_id: number; credential: ProviderCredentialSummary }>(
      "/saas/provider-credentials/gemini",
      {
        method: "DELETE",
        headers: bearer(token),
      },
    );
  },
  simulate(apiKey: string, tokenAddress: string, amount: number) {
    return request<SimulationResult>("/simulate/order", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...apiKeyHeader(apiKey) },
      body: JSON.stringify({ token_address: tokenAddress, amount, side: "BUY" }),
    });
  },
  checkout(token: string, planCode: "pro" | "enterprise") {
    return request<{ url: string; org_id: number; plan: string }>("/billing/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify({ plan_code: planCode }),
    });
  },
  invoices(token: string) {
    return request<{ org_id: number; invoices: Array<{ id?: string; url?: string; amount?: number }> }>(
      "/saas/invoices",
      { headers: bearer(token) },
    );
  },
  auditLog(token: string) {
    return request<{ org_id: number; events: AuditEvent[]; limit: number; offset: number }>(
      "/saas/audit-log",
      { headers: bearer(token) },
    );
  },
  proposals(token: string) {
    return request<{ org_id: number; proposals: CopilotProposal[] }>("/copilot/proposals", {
      headers: bearer(token),
    });
  },
  tokenDiscoveryHistory(token: string) {
    return request<{ org_id: number; history: TokenDiscoveryHistoryRecord[]; limit: number; offset: number }>(
      "/saas/token-discovery-history?limit=50",
      { headers: bearer(token) },
    );
  },
  approveProposal(token: string, id: number) {
    return request<{ proposal: CopilotProposal }>(`/copilot/proposals/${id}/approve`, {
      method: "POST",
      headers: bearer(token),
    });
  },
  approveProposalWithWallet(
    token: string,
    id: number,
    payload: {
      wallet_address?: string;
      wallet_chain_id?: string;
      signed_message?: string;
      wallet_signature?: string;
    },
  ) {
    return request<{ proposal: CopilotProposal }>(`/copilot/proposals/${id}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify(payload),
    });
  },
  scanCopilot(
    token: string,
    payload: {
      query?: string;
      amount_usd?: number;
      limit?: number;
      min_volume?: number;
      min_momentum?: number;
      min_liquidity?: number;
      execution_mode?: "paper_auto" | "copilot" | "real_approval";
      wallet_address?: string;
      wallet_chain_id?: string;
    } = {},
  ) {
    return request<CopilotScanResponse>("/copilot/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify(payload),
    });
  },
  artifacts() {
    return request<BillableArtifact[]>(`${API_ROOT}/api/payments/catalog`);
  },
  paymentOrders(token: string) {
    return request<PaymentIntentRecord[]>(`${API_ROOT}/api/payments/orders`, {
      headers: bearer(token),
    });
  },
  createPaymentIntent(token: string, artifactId: string, buyerAddress: string) {
    return request<PaymentIntentResponse>(`${API_ROOT}/api/payments/intent`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify({ artifact_id: artifactId, buyer_address: buyerAddress }),
    });
  },
  verifyPayment(token: string, paymentId: string, txHash: string) {
    return request<PaymentVerificationResponse>(`${API_ROOT}/api/payments/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify({ payment_id: paymentId, tx_hash: txHash }),
    });
  },
  executePayment(token: string, artifactId: string, paymentId: string) {
    return request<JobExecutionResponse>(`${API_ROOT}/api/payments/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify({ artifact_id: artifactId, payment_id: paymentId }),
    });
  },
  nexusJob(token: string, jobId: string) {
    return request<NexusJob>(`${API_ROOT}/api/jobs/${jobId}`, {
      headers: bearer(token),
    });
  },
  arcJobs(token: string) {
    return request<ArcJobProof[]>(`${API_ROOT}/api/arc/jobs?limit=5`, {
      headers: bearer(token),
    });
  },
  agentChat(
    token: string,
    message: string,
    history: AgentChatTurn[],
  ) {
    return request<AgentChatResponse>(`${API_ROOT}/api/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearer(token) },
      body: JSON.stringify({
        message,
        history,
      }),
    });
  },
  health() {
    return request<Health>(`${API_ROOT}/health`);
  },
};
