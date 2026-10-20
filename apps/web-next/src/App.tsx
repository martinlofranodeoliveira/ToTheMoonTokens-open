import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bell,
  BookOpen,
  Bot,
  Check,
  Copy,
  CreditCard,
  Database,
  Download,
  FileText,
  Gauge,
  KeyRound,
  LineChart,
  Lock,
  LogOut,
  Menu,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  Shield,
  Terminal,
  Wallet,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import {
  API_BASE,
  Account,
  AgentChatTurn,
  ArcJobProof,
  ApiKeyRecord,
  AuditEvent,
  BillableArtifact,
  CopilotProposal,
  Dashboard,
  Health,
  NexusJob,
  PaymentIntentRecord,
  TokenDiscoveryHistoryRecord,
  Usage,
  api,
} from "./lib/api";

type View = "guide" | "overview" | "audit" | "paper" | "bot" | "market" | "pay" | "keys" | "log" | "docs" | "settings";
type PublicView = "landing" | "login" | "signup" | "legal" | "privacy" | "terms" | "risk";
type Notice = { type: "ok" | "error"; text: string } | null;
type DataMode = "live" | "degraded";
type BotMode = "sim" | "copilot" | "auto";
type ScannerExecutionMode = "paper_auto" | "copilot" | "real_approval";
type WalletStatus = "idle" | "connected" | "missing" | "error";
type InvoiceSummary = { id?: string; url?: string; amount?: number };
type ScannerRunEvidence = {
  id: number;
  query: string;
  executionMode: ScannerExecutionMode;
  status: string;
  candidateCount: number;
  blockedCount: number;
  queuedCount: number;
  paperExecutedCount: number;
  completedAt: string | null;
  created: CopilotProposal[];
  skipped: Array<{ token_address: string; reason: string; score: number }>;
  ranAt: string;
};
type EthereumProvider = {
  request: (args: { method: string; params?: unknown[] | Record<string, unknown> }) => Promise<unknown>;
  isMetaMask?: boolean;
};

const money = (value = 0) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);

const compactNumber = (value = 0) => new Intl.NumberFormat("en-US", { notation: "compact" }).format(value);

const proposalScorePct = (score = 0) => Math.round(score <= 1 ? score * 100 : score);

const normalizedProposalStatus = (status: string) => status.toLowerCase().replaceAll("_", "-");

const isRejectedProposalStatus = (status: string) => {
  const normalized = normalizedProposalStatus(status);
  return normalized.includes("reject") || normalized.includes("cancel");
};

const isPendingProposalStatus = (status: string) => normalizedProposalStatus(status) === "pending";

const proposalStatusLabel = (status: string) => {
  const normalized = normalizedProposalStatus(status);
  if (normalized === "pending") return "Pending approval";
  if (normalized === "executed") return "Paper executed";
  if (normalized === "approved-manual-required") return "Manual approval recorded";
  if (normalized.includes("reject")) return "Rejected - no trade";
  if (normalized.includes("cancel")) return "Cancelled - no trade";
  return status.replaceAll("_", " ");
};

const proposalStatusTone = (status: string) => {
  const normalized = normalizedProposalStatus(status);
  if (normalized === "pending") return "warn";
  if (normalized === "executed") return "ok";
  if (isRejectedProposalStatus(status)) return "err";
  return "neut";
};

const proposalStatusFeedback = (proposal: CopilotProposal) => {
  const normalized = normalizedProposalStatus(proposal.status);
  if (normalized.includes("reject")) {
    return "Rejected by reviewer. Kept as Mode 2 no-execution evidence; no paper trade was created.";
  }
  if (normalized.includes("cancel")) {
    return "Cancelled before approval. Kept visible so it is not confused with executed paper trades.";
  }
  if (normalized === "approved-manual-required") {
    return "Manual approval recorded; real autonomous submission remains locked.";
  }
  return null;
};

const proposalActionLabel = (proposal: CopilotProposal) => {
  const normalized = normalizedProposalStatus(proposal.status);
  if (normalized === "pending") return "Approve proposal";
  if (normalized === "executed") return "Paper executed";
  if (isRejectedProposalStatus(proposal.status)) return proposalStatusLabel(proposal.status);
  return "Not pending";
};

const sparkSeries = (values: number[], fallback = 0) => {
  const series = values.length ? values : [fallback];
  return series.length > 1 ? series : [series[0], series[0]];
};

const shortId = (value: string | null | undefined) => (value ? `${value.slice(0, 10)}...${value.slice(-6)}` : "not issued");

const scannerReadiness = ({
  geminiConfigured,
  health,
  hasApiKey = true,
  blockers = [],
}: {
  geminiConfigured: boolean;
  health: Health;
  hasApiKey?: boolean;
  blockers?: string[];
}) => {
  const blockedReasons = [
    !geminiConfigured ? "Gemini key ausente" : null,
    !hasApiKey ? "API key de simulacao ausente" : null,
    !health.ok ? "API instavel" : null,
    health.orderSubmissionEnabled ? "mainnet armado contra politica paper" : null,
    ...blockers,
  ].filter((reason): reason is string => Boolean(reason));
  const ready = blockedReasons.length === 0;

  return {
    ready,
    blockers: blockedReasons,
    status: ready
      ? "Scanner pronto para Mode 1 paper auto ou Mode 2 fila de aprovacao paper."
      : `Scanner pausado: ${blockedReasons.join(", ")}.`,
  };
};

const compactDate = (value: string | null | undefined) => {
  if (!value) return "never";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(
    new Date(value),
  );
};

const looksLikeKeyHint = (value: string) => value.includes("...") || value.includes("…") || value.includes("•••");

const emptyAccount: Account = {
  email: "",
  org_id: 0,
  organization: "Loading workspace",
  plan: "free",
  created_at: "",
  real_mode_enabled: false,
  real_mode_approved_at: null,
  real_mode_daily_limit_usd: 50,
  bot_consecutive_failures: 0,
};

const emptyDashboard: Dashboard = {
  email: "",
  org_id: 0,
  plan: "free",
  requests_this_month: 0,
  simulated_volume_usd: 0,
  active_api_keys: 0,
  total_simulated_trades: 0,
  total_volume_usd: 0,
  total_fees_usd: 0,
  realized_pnl_usd: 0,
  unrealized_pnl_usd: 0,
  last_30_days_chart_points: [],
};

const emptyUsage: Usage = {
  requests_today: 0,
  requests_month: 0,
  plan: "free",
  quota: { simulate_order: 0, token_audit: 0, active_api_keys: 0 },
};

const publicViewFromPath = (): PublicView => {
  if (window.location.pathname === "/login") return "login";
  if (window.location.pathname === "/signup") return "signup";
  if (window.location.pathname === "/legal") return "legal";
  if (window.location.pathname === "/privacy") return "privacy";
  if (window.location.pathname === "/terms") return "terms";
  if (window.location.pathname === "/risk") return "risk";
  return "landing";
};

const publicPath = (view: PublicView) => (view === "landing" ? "/" : `/${view}`);

const isWorkspacePath = () => {
  const pathname = window.location.pathname.replace(/\/+$/, "") || "/";
  return pathname === "/app" || pathname.startsWith("/app/");
};

const workspacePathByView: Record<View, string> = {
  guide: "",
  overview: "overview",
  audit: "audit",
  paper: "paper",
  bot: "bot",
  market: "market",
  pay: "nanopayments",
  keys: "api-keys",
  log: "audit-log",
  docs: "docs",
  settings: "settings",
};

const workspaceViewByPath: Record<string, View> = {
  "": "guide",
  setup: "guide",
  guide: "guide",
  overview: "overview",
  audit: "audit",
  "production-audit": "audit",
  paper: "paper",
  "paper-trading": "paper",
  bot: "bot",
  "bot-modes": "bot",
  market: "market",
  "market-data": "market",
  nanopayments: "pay",
  pay: "pay",
  "api-keys": "keys",
  keys: "keys",
  "audit-log": "log",
  log: "log",
  docs: "docs",
  sdk: "docs",
  settings: "settings",
};

const workspacePath = (view: View) => {
  const section = workspacePathByView[view];
  return section ? `/app/${section}` : "/app";
};

const workspaceRouteFromPath = () => {
  const pathname = window.location.pathname.replace(/\/+$/, "") || "/";
  if (pathname !== "/app" && !pathname.startsWith("/app/")) return { view: "guide" as View, valid: false };
  const section = pathname.slice("/app".length).replace(/^\//, "").split("/")[0];
  const view = workspaceViewByPath[section];
  return view ? { view, valid: true } : { view: "guide" as View, valid: false };
};

const workspaceViewFromPath = (): View => {
  return workspaceRouteFromPath().view;
};

const emptyHealth: Health = {
  ok: false,
  app: "ToTheMoonTokens",
  mode: "paper",
  orderSubmissionEnabled: false,
  providers: {},
};

const defaultChatMessages: AgentChatTurn[] = [
  {
    role: "assistant",
    text: "O scanner e o operador principal. O chat fica aqui só para diagnosticar fila, risco e proximos testes quando voce precisar.",
  },
];

const AUTOPILOT_INTERVAL_MS = 120_000;

const getEthereumProvider = () =>
  typeof window === "undefined"
    ? undefined
    : (window as typeof window & { ethereum?: EthereumProvider }).ethereum;

function App() {
  const [publicView, setPublicView] = useState<PublicView>(() => publicViewFromPath());
  const [view, setView] = useState<View>(() => workspaceViewFromPath());
  const [botMode, setBotMode] = useState<BotMode>("sim");
  const [jwt, setJwt] = useState(() => localStorage.getItem("ttm_jwt"));
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem("ttm_api_key") || "");
  const [account, setAccount] = useState<Account | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [keys, setKeys] = useState<ApiKeyRecord[]>([]);
  const [proposals, setProposals] = useState<CopilotProposal[]>([]);
  const [tokenHistory, setTokenHistory] = useState<TokenDiscoveryHistoryRecord[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [artifacts, setArtifacts] = useState<BillableArtifact[]>([]);
  const [paymentOrders, setPaymentOrders] = useState<PaymentIntentRecord[]>([]);
  const [selectedArtifactId, setSelectedArtifactId] = useState("artifact_delivery_packet");
  const [selectedPaymentId, setSelectedPaymentId] = useState<string | null>(null);
  const [nexusJob, setNexusJob] = useState<NexusJob | null>(null);
  const [arcProofs, setArcProofs] = useState<ArcJobProof[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [keyName, setKeyName] = useState("production-bot");
  const [geminiApiKeyInput, setGeminiApiKeyInput] = useState("");
  const [geminiModel, setGeminiModel] = useState("gemini-3.1-pro-preview");
  const [chatMessages, setChatMessages] = useState<AgentChatTurn[]>(defaultChatMessages);
  const [chatInput, setChatInput] = useState("Explique o ultimo ciclo do scanner e o proximo paper test seguro.");
  const [walletAddress, setWalletAddress] = useState("");
  const [walletChainId, setWalletChainId] = useState("");
  const [walletStatus, setWalletStatus] = useState<WalletStatus>("idle");
  const [scannerQuery, setScannerQuery] = useState("USDC");
  const [scannerRunEvidence, setScannerRunEvidence] = useState<ScannerRunEvidence | null>(null);
  const [tokenAddress, setTokenAddress] = useState("0xMOON");
  const [amount, setAmount] = useState(100);
  const [botAutopilotEnabled, setBotAutopilotEnabled] = useState(() => localStorage.getItem("ttm_bot_autopilot") !== "off");
  const [autopilotStatus, setAutopilotStatus] = useState<"idle" | "running" | "paused" | "error">("paused");
  const [autopilotLastRunAt, setAutopilotLastRunAt] = useState<string | null>(null);
  const [autopilotError, setAutopilotError] = useState<string | null>(null);
  const [autopilotRuns, setAutopilotRuns] = useState(0);
  const [busy, setBusy] = useState(false);
  const [checkoutPlan, setCheckoutPlan] = useState<"pro" | "enterprise" | null>(null);
  const [loadingData, setLoadingData] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [dataError, setDataError] = useState<string | null>(null);
  const [dataMode, setDataMode] = useState<DataMode>("live");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [mobileNavFocusTarget, setMobileNavFocusTarget] = useState<"close" | "active">("close");
  const mobileMenuButtonRef = useRef<HTMLButtonElement | null>(null);
  const mobileCloseButtonRef = useRef<HTMLButtonElement | null>(null);
  const noticeTimer = useRef<number | null>(null);
  const autopilotInFlight = useRef(false);
  const lastAutoScanAt = useRef(0);

  const isSignedIn = Boolean(jwt);
  const currentAccount = account || emptyAccount;
  const currentDashboard = dashboard || emptyDashboard;
  const currentUsage = usage || emptyUsage;
  const selectedArtifact = artifacts.find((artifact) => artifact.id === selectedArtifactId) || artifacts[0] || null;
  const activePayment = paymentOrders.find((payment) => payment.payment_id === selectedPaymentId) || paymentOrders[0] || null;
  const activeArcProof = activePayment
    ? arcProofs.find((proof) => proof.payment_id === activePayment.payment_id || proof.job_id === activePayment.job_id) || null
    : null;
  const [paymentsClearedByChat, setPaymentsClearedByChat] = useState(false);
  const activeApiKeys = currentDashboard.active_api_keys || keys.filter((key) => !key.revoked_at).length;

  const chartData = useMemo(() => currentDashboard.last_30_days_chart_points || [], [currentDashboard.last_30_days_chart_points]);

  function focusWorkspaceContent(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    if (window.location.hash !== "#workspace-content") window.history.pushState({}, "", "#workspace-content");
    document.getElementById("workspace-content")?.focus({ preventScroll: true });
  }

  function show(type: "ok" | "error", text: string) {
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    setNotice({ type, text });
    noticeTimer.current = window.setTimeout(() => {
      setNotice(null);
      noticeTimer.current = null;
    }, 3200);
  }

  function clearWorkspace(message?: string) {
    setAccount(null);
    setDashboard(null);
    setUsage(null);
    setKeys([]);
    setProposals([]);
    setTokenHistory([]);
    setAuditEvents([]);
    setInvoices([]);
    setHealth(null);
    setArtifacts([]);
    setPaymentOrders([]);
    setSelectedPaymentId(null);
    setNexusJob(null);
    setArcProofs([]);
    setChatMessages(defaultChatMessages);
    setChatInput("Explique o ultimo ciclo do scanner e o proximo paper test seguro.");
    setAutopilotStatus("paused");
    setAutopilotError(null);
    setAutopilotRuns(0);
    setAutopilotLastRunAt(null);
    setDataMode("live");
    setDataError(message || null);
  }

  function navigatePublic(nextView: PublicView) {
    setPublicView(nextView);
    window.history.pushState({}, "", publicPath(nextView));
  }

  function navigateWorkspace(nextView: View, mode: "push" | "replace" = "push") {
    setView(nextView);
    const nextPath = workspacePath(nextView);
    if (window.location.pathname === nextPath) return;
    if (mode === "replace") window.history.replaceState({}, "", nextPath);
    else window.history.pushState({}, "", nextPath);
  }

  async function loadLifecycle(activeToken = jwt, jobId?: string | null, forcePaymentRefresh = false) {
    if (!activeToken) return false;

    const [artifactResult, orderResult, proofResult] = await Promise.allSettled([
      api.artifacts(),
      api.paymentOrders(activeToken),
      api.arcJobs(activeToken),
    ]);
    if (artifactResult.status === "fulfilled") {
      setArtifacts(artifactResult.value);
      setSelectedArtifactId((current) => (artifactResult.value.some((artifact) => artifact.id === current) ? current : artifactResult.value[0]?.id || current));
    } else {
      setArtifacts([]);
    }

    const selectedOrder =
      orderResult.status === "fulfilled"
        ? orderResult.value.find((order) => order.job_id === jobId) ||
          orderResult.value.find((order) => order.payment_id === selectedPaymentId) ||
          orderResult.value[0] ||
          null
        : null;

    if (orderResult.status === "fulfilled" && (!paymentsClearedByChat || forcePaymentRefresh)) {
      setPaymentOrders(orderResult.value);
      setSelectedPaymentId(selectedOrder?.payment_id || null);
    } else if (orderResult.status === "rejected") {
      setPaymentOrders([]);
      setSelectedPaymentId(null);
    }

    if (proofResult.status === "fulfilled") setArcProofs(proofResult.value);
    else setArcProofs([]);

    const latestJobId = jobId || selectedOrder?.job_id || null;
    if (latestJobId) {
      try {
        setNexusJob(await api.nexusJob(activeToken, latestJobId));
      } catch {
        setNexusJob(null);
      }
    } else {
      setNexusJob(null);
    }

    return [artifactResult, orderResult, proofResult].some((result) => result.status === "rejected");
  }

  async function loadAll(activeToken = jwt) {
    if (!activeToken) {
      clearWorkspace();
      return;
    }

    setLoadingData(true);
    setDataError(null);
    try {
      const [
        healthResult,
        accountResult,
        dashboardResult,
        usageResult,
        keyResult,
        proposalResult,
        tokenHistoryResult,
        auditResult,
        invoiceResult,
      ] = await Promise.allSettled([
        api.health(),
        api.account(activeToken),
        api.dashboard(activeToken),
        api.usage(activeToken),
        api.apiKeys(activeToken),
        api.proposals(activeToken),
        api.tokenDiscoveryHistory(activeToken),
        api.auditLog(activeToken),
        api.invoices(activeToken),
      ]);

      if (healthResult.status === "fulfilled") setHealth(healthResult.value);
      else setHealth(null);
      if (accountResult.status === "fulfilled") setAccount(accountResult.value);
      else setAccount(null);
      if (dashboardResult.status === "fulfilled") setDashboard(dashboardResult.value);
      else setDashboard(null);
      if (usageResult.status === "fulfilled") setUsage(usageResult.value);
      else setUsage(null);
      if (keyResult.status === "fulfilled") setKeys(keyResult.value);
      else setKeys([]);
      if (proposalResult.status === "fulfilled") setProposals(proposalResult.value.proposals);
      else setProposals([]);
      if (tokenHistoryResult.status === "fulfilled") setTokenHistory(tokenHistoryResult.value.history);
      else setTokenHistory([]);
      if (auditResult.status === "fulfilled") setAuditEvents(auditResult.value.events);
      else setAuditEvents([]);
      if (invoiceResult.status === "fulfilled") setInvoices(invoiceResult.value.invoices);
      else setInvoices([]);

      const lifecycleFallback = view === "pay" ? await loadLifecycle(activeToken) : false;
      if (view !== "pay") {
        setArtifacts([]);
        setPaymentOrders([]);
        setSelectedPaymentId(null);
        setNexusJob(null);
        setArcProofs([]);
      }
      const failures = [
        healthResult,
        accountResult,
        dashboardResult,
        usageResult,
        keyResult,
        proposalResult,
        tokenHistoryResult,
        auditResult,
        invoiceResult,
      ].filter(
        (result) => result.status === "rejected",
      );

      if (failures.length > 0 || lifecycleFallback) {
        const firstFailure = failures[0];
        const message =
          firstFailure?.status === "rejected" && firstFailure.reason instanceof Error
            ? firstFailure.reason.message
            : "Some live data could not be refreshed.";
        setDataError(message);
        setDataMode("degraded");
      } else {
        setDataMode("live");
      }
    } finally {
      setLoadingData(false);
    }
  }

  async function signIn(mode: "login" | "signup") {
    setBusy(true);
    try {
      if (mode === "signup") await api.signup(email.trim(), password);
      const result = await api.login(email.trim(), password);
      localStorage.setItem("ttm_jwt", result.access_token);
      setJwt(result.access_token);
      navigateWorkspace("guide");
      await loadAll(result.access_token);
      show("ok", mode === "signup" ? "Conta criada." : "Login realizado.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Authentication failed.";
      localStorage.removeItem("ttm_jwt");
      setJwt(null);
      clearWorkspace(message);
      show("error", message);
    } finally {
      setBusy(false);
    }
  }

  function signOut() {
    localStorage.removeItem("ttm_jwt");
    sessionStorage.removeItem("ttm_api_key");
    setJwt(null);
    setApiKey("");
    setWalletAddress("");
    setWalletChainId("");
    setWalletStatus("idle");
    clearWorkspace();
    navigatePublic("landing");
    show("ok", "Sessao encerrada.");
  }

  async function createKey() {
    if (!jwt) return;

    setBusy(true);
    try {
      const created = await api.createApiKey(jwt, keyName.trim() || "production-bot");
      sessionStorage.setItem("ttm_api_key", created.plaintext);
      setApiKey(created.plaintext);
      await loadAll();
      show("ok", "API key created.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Key creation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function revokeKey(id: number) {
    if (!jwt) return;

    setBusy(true);
    try {
      await api.revokeApiKey(jwt, id);
      await loadAll();
      show("ok", "API key revoked.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Revoke failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveGeminiCredential() {
    if (!jwt) return;
    const cleanKey = geminiApiKeyInput.trim();
    if (!cleanKey) {
      show("error", "Cole sua Gemini API key.");
      return;
    }
    if (looksLikeKeyHint(cleanKey)) {
      show("error", "Cole a Gemini API key completa, nao o hint mascarado.");
      return;
    }

    setBusy(true);
    try {
      await api.saveGeminiCredential(jwt, cleanKey, geminiModel.trim() || "gemini-3.1-pro-preview");
      setGeminiApiKeyInput("");
      await loadAll();
      show("ok", "Gemini API key salva na sua conta.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Falha ao salvar Gemini API key.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteGeminiCredential() {
    if (!jwt) return;

    setBusy(true);
    try {
      await api.deleteGeminiCredential(jwt);
      await loadAll();
      show("ok", "Gemini API key removida.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Falha ao remover Gemini API key.");
    } finally {
      setBusy(false);
    }
  }

  async function copyActiveKey() {
    if (!apiKey) return;
    try {
      await navigator.clipboard.writeText(apiKey);
      show("ok", "API key copied.");
    } catch {
      show("error", "Copy failed. Select the key manually.");
    }
  }

  async function runSimulation() {
    if (!apiKey) {
      show("error", "Set an API key.");
      return;
    }

    setBusy(true);
    try {
      await api.simulate(apiKey, tokenAddress.trim(), amount);
      await loadAll();
      show("ok", "Simulation recorded.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Simulation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function approveProposal(id: number) {
    if (!jwt) return;
    const proposal = proposals.find((item) => item.id === id);

    setBusy(true);
    try {
      if (proposal?.mode === "real") {
        if (walletStatus !== "connected" || !walletAddress) {
          show("error", "Conecte a MetaMask para registrar aprovacao humana de Mode 2.");
          return;
        }
        const provider = getEthereumProvider();
        if (!provider) {
          setWalletStatus("missing");
          show("error", "MetaMask ou carteira compativel nao detectada neste navegador.");
          return;
        }
        const signedMessage = [
          "TTM Mode 2 manual approval",
          `Proposal: ${proposal.id}`,
          `Token: ${proposal.symbol || proposal.token_address}`,
          `Chain: ${proposal.chain}`,
          `Amount USD: ${proposal.amount_usd}`,
          "No autonomous order submission is authorized by this signature.",
        ].join("\n");
        const signature = await provider.request({
          method: "personal_sign",
          params: [signedMessage, walletAddress],
        });
        await api.approveProposalWithWallet(jwt, id, {
          wallet_address: walletAddress,
          wallet_chain_id: walletChainId,
          signed_message: signedMessage,
          wallet_signature: typeof signature === "string" ? signature : String(signature),
        });
      } else {
        await api.approveProposal(jwt, id);
      }
      await loadAll();
      show("ok", proposal?.mode === "real" ? "Aprovacao manual registrada; ordem real continua fora do runtime." : "Proposal executed.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Approval failed.");
    } finally {
      setBusy(false);
    }
  }

  async function runCopilotScan(
    executionMode: ScannerExecutionMode = "paper_auto",
    options: { silent?: boolean } = {},
  ) {
    if (!jwt) return;
    if (executionMode === "real_approval" && (!walletAddress || walletStatus !== "connected")) {
      show("error", "Conecte a MetaMask antes de criar uma fila de aprovacao real.");
      return;
    }

    if (options.silent) {
      setAutopilotStatus("running");
      setAutopilotError(null);
    } else {
      setBusy(true);
    }
    try {
      const query = scannerQuery.trim() || "USDC";
      const result = await api.scanCopilot(jwt, {
        query,
        amount_usd: amount,
        limit: 8,
        execution_mode: executionMode,
        wallet_address: executionMode === "real_approval" ? walletAddress : undefined,
        wallet_chain_id: executionMode === "real_approval" ? walletChainId : undefined,
      });
      setScannerRunEvidence({
        id: result.scanner_run.id,
        query: result.scanner_run.query,
        executionMode: result.scanner_run.execution_mode,
        status: result.scanner_run.status,
        candidateCount: result.scanner_run.candidate_count,
        blockedCount: result.scanner_run.blocked_count,
        queuedCount: result.scanner_run.queued_count,
        paperExecutedCount: result.scanner_run.paper_executed_count,
        completedAt: result.scanner_run.completed_at,
        created: result.created,
        skipped: result.skipped,
        ranAt: result.scanner_run.completed_at || result.scanner_run.created_at,
      });
      await loadAll();
      setAutopilotLastRunAt(new Date().toISOString());
      setAutopilotRuns((current) => current + 1);
      setAutopilotStatus("idle");
      setAutopilotError(null);
      const action =
        executionMode === "paper_auto"
          ? "executadas em paper"
          : executionMode === "real_approval"
            ? "criadas para aprovacao real"
            : "criadas para aprovacao";
      if (!options.silent) show("ok", `${result.created.length} propostas ${action}; ${result.skipped.length} bloqueadas.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Scanner failed.";
      setAutopilotStatus("error");
      setAutopilotError(message);
      if (!options.silent) show("error", message);
    } finally {
      if (!options.silent) setBusy(false);
    }
  }

  async function sendAgentMessage(messageOverride?: string) {
    if (!jwt) return;
    const message = (messageOverride || chatInput).trim();
    if (!message) {
      show("error", "Digite uma pergunta para o bot.");
      return;
    }

    const geminiConfigured = Boolean(currentAccount.provider_credentials?.gemini?.configured);
    if (!geminiConfigured) {
      show("error", "Configure sua Gemini API key em Settings antes de usar o bot.");
      navigateWorkspace("settings");
      return;
    }

    const history = chatMessages.filter((turn) => turn.text.trim()).slice(-8);
    const userTurn: AgentChatTurn = { role: "user", text: message };
    setChatMessages((current) => [...current, userTurn]);
    setChatInput("");
    setBusy(true);
    try {
      const response = await api.agentChat(jwt, message, history);
      setChatMessages((current) => [...current, { role: "assistant", text: response.reply }]);
      if (response.orders) {
        setPaymentOrders(response.orders);
        setSelectedPaymentId((current) => (response.orders.some((order) => order.payment_id === current) ? current : response.orders[0]?.payment_id || null));
        if (response.orders.length === 0) {
          setPaymentsClearedByChat(true);
          setNexusJob(null);
          setArcProofs([]);
        } else {
          setPaymentsClearedByChat(false);
        }
      }
      show("ok", "Bot respondeu.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Falha ao conversar com o bot.");
      setChatMessages((current) => [
        ...current,
        {
          role: "assistant",
          text: "Nao consegui responder agora. Confira se a Gemini API key esta ativa em Settings e tente novamente.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function connectWallet() {
    const provider = getEthereumProvider();
    if (!provider) {
      setWalletStatus("missing");
      show("error", "MetaMask ou carteira compativel nao detectada neste navegador.");
      return;
    }

    setBusy(true);
    try {
      const accounts = await provider.request({ method: "eth_requestAccounts" });
      const chainId = await provider.request({ method: "eth_chainId" }).catch(() => "");
      const firstAccount = Array.isArray(accounts) ? String(accounts[0] || "") : "";
      if (!firstAccount) throw new Error("Nenhuma conta retornada pela carteira.");
      setWalletAddress(firstAccount);
      setWalletChainId(typeof chainId === "string" ? chainId : "");
      setWalletStatus("connected");
      show("ok", "Carteira conectada para assinatura manual.");
    } catch (error) {
      setWalletStatus("error");
      show("error", error instanceof Error ? error.message : "Falha ao conectar carteira.");
    } finally {
      setBusy(false);
    }
  }

  async function checkout(planCode: "pro" | "enterprise") {
    if (!jwt) return;

    setBusy(true);
    setCheckoutPlan(planCode);
    try {
      const result = await api.checkout(jwt, planCode);
      window.open(result.url, "_blank", "noopener,noreferrer");
      show("ok", "Checkout opened.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Checkout failed.");
    } finally {
      setBusy(false);
      setCheckoutPlan(null);
    }
  }

  async function createPaymentIntent() {
    if (!jwt || !selectedArtifact) return;
    if (!account?.email) {
      show("error", "Account data is still loading.");
      return;
    }

    setBusy(true);
    try {
      const intent = await api.createPaymentIntent(jwt, selectedArtifact.id, account.email);
      setPaymentsClearedByChat(false);
      setSelectedPaymentId(intent.payment_id);
      await loadLifecycle(jwt, intent.job_id, true);
      show("ok", "Payment requirement created.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Payment setup failed.");
    } finally {
      setBusy(false);
    }
  }

  async function verifyPayment() {
    if (!jwt || !activePayment) return;

    setBusy(true);
    try {
      await api.verifyPayment(jwt, activePayment.payment_id, "0xMockTransactionHash");
      await loadLifecycle(jwt, activePayment.job_id);
      show("ok", "Payment verified.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Payment verification failed.");
    } finally {
      setBusy(false);
    }
  }

  async function unlockDelivery() {
    if (!jwt || !activePayment) return;

    setBusy(true);
    try {
      await api.executePayment(jwt, activePayment.artifact_id, activePayment.payment_id);
      await loadLifecycle(jwt, activePayment.job_id);
      show("ok", "Delivery unlocked.");
    } catch (error) {
      show("error", error instanceof Error ? error.message : "Delivery unlock failed.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    loadAll().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!jwt) return;
    const route = workspaceRouteFromPath();
    setView(route.view);
    if (!route.valid) window.history.replaceState({}, "", workspacePath(route.view));
  }, [jwt]);

  useEffect(() => {
    if (jwt && !isWorkspacePath()) window.history.replaceState({}, "", workspacePath(view));
    if (!jwt && isWorkspacePath()) window.history.replaceState({}, "", "/");
  }, [jwt, view]);

  useEffect(() => {
    if (!jwt || view !== "pay") return;
    loadLifecycle(jwt).catch(() => undefined);
  }, [jwt, view]);

  useEffect(() => {
    document.title = isSignedIn ? `${viewTitle(view)} | ToTheMoonTokens` : "ToTheMoonTokens";
  }, [isSignedIn, view]);

  useEffect(() => {
    return () => {
      if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    };
  }, []);

  useEffect(() => {
    const configuredModel = account?.provider_credentials?.gemini?.model;
    if (configuredModel) setGeminiModel(configuredModel);
  }, [account?.provider_credentials?.gemini?.model]);

  useEffect(() => {
    if (!jwt) return;
    const controller = new AbortController();
    fetch(`${API_BASE}/copilot/stream?once=true`, {
      headers: { Authorization: `Bearer ${jwt}` },
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.body) return;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const events = buffer.split("\n\n");
          buffer = events.pop() || "";
          for (const event of events) {
            const line = event.split("\n").find((item) => item.startsWith("data: "));
            if (!line) continue;
            const proposal = JSON.parse(line.slice(6)) as CopilotProposal;
            setProposals((current) => [proposal, ...current.filter((item) => item.id !== proposal.id)]);
          }
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [jwt]);

  useEffect(() => {
    localStorage.setItem("ttm_bot_autopilot", botAutopilotEnabled ? "on" : "off");
  }, [botAutopilotEnabled]);

  useEffect(() => {
    const geminiConfigured = Boolean(account?.provider_credentials?.gemini?.configured);
    const canRunAutopilot =
      Boolean(jwt) &&
      botMode === "sim" &&
      botAutopilotEnabled &&
      geminiConfigured &&
      Boolean(health?.ok) &&
      !health?.orderSubmissionEnabled &&
      activeApiKeys > 0;

    if (!botAutopilotEnabled || botMode !== "sim" || !canRunAutopilot) {
      setAutopilotStatus((current) => (current === "running" ? current : "paused"));
      return undefined;
    }

    setAutopilotStatus((current) => (current === "running" ? current : "idle"));
    const runIfDue = () => {
      if (document.visibilityState === "hidden" || autopilotInFlight.current) return;
      const now = Date.now();
      if (lastAutoScanAt.current && now - lastAutoScanAt.current < AUTOPILOT_INTERVAL_MS - 1_000) return;
      lastAutoScanAt.current = now;
      autopilotInFlight.current = true;
      void runCopilotScan("paper_auto", { silent: true }).finally(() => {
        autopilotInFlight.current = false;
      });
    };

    const delay = lastAutoScanAt.current
      ? Math.max(1_500, AUTOPILOT_INTERVAL_MS - (Date.now() - lastAutoScanAt.current))
      : 1_500;
    const firstRun = window.setTimeout(runIfDue, delay);
    const interval = window.setInterval(runIfDue, AUTOPILOT_INTERVAL_MS);
    return () => {
      window.clearTimeout(firstRun);
      window.clearInterval(interval);
    };
  }, [
    jwt,
    botMode,
    botAutopilotEnabled,
    account?.provider_credentials?.gemini?.configured,
    health?.ok,
    health?.orderSubmissionEnabled,
    activeApiKeys,
    scannerQuery,
    amount,
  ]);

  useEffect(() => {
    const onPopState = () => {
      if (jwt) {
        const route = workspaceRouteFromPath();
        setView(route.view);
        if (!route.valid) window.history.replaceState({}, "", workspacePath(route.view));
      }
      else setPublicView(publicViewFromPath());
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [jwt]);

  useEffect(() => {
    if (!mobileNavOpen) return undefined;

    if (mobileNavFocusTarget === "close") window.setTimeout(() => mobileCloseButtonRef.current?.focus(), 0);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeMobileNav({ restoreFocus: true });
        return;
      }
      if (event.key !== "Tab") return;

      const navigation = document.getElementById("workspace-navigation");
      if (!navigation) return;

      const focusableSelector = [
        "button:not([disabled])",
        "a[href]",
        "input:not([disabled])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        '[tabindex]:not([tabindex="-1"])',
      ].join(", ");
      const focusableItems = Array.from(
        navigation.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((element) => !element.hasAttribute("aria-hidden"));
      const firstFocusable = focusableItems[0];
      const lastFocusable = focusableItems[focusableItems.length - 1];
      if (!firstFocusable || !lastFocusable) return;

      if (!navigation.contains(document.activeElement)) {
        event.preventDefault();
        firstFocusable.focus({ preventScroll: true });
        return;
      }

      if (event.shiftKey && document.activeElement === firstFocusable) {
        event.preventDefault();
        lastFocusable.focus({ preventScroll: true });
      } else if (!event.shiftKey && document.activeElement === lastFocusable) {
        event.preventDefault();
        firstFocusable.focus({ preventScroll: true });
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileNavOpen, mobileNavFocusTarget]);

  const closeMobileNav = ({ restoreFocus = false }: { restoreFocus?: boolean } = {}) => {
    setMobileNavOpen(false);
    if (!restoreFocus) return;
    window.requestAnimationFrame(() => mobileMenuButtonRef.current?.focus({ preventScroll: true }));
  };

  if (!isSignedIn) {
    return (
      <>
        {publicView === "landing" ? (
          <LandingPage onLogin={() => navigatePublic("login")} onSignup={() => navigatePublic("signup")} onLegal={navigatePublic} />
        ) : publicView === "legal" || publicView === "privacy" || publicView === "terms" || publicView === "risk" ? (
          <LegalPage view={publicView} onNavigate={navigatePublic} />
        ) : (
          <MarketingAuthPage
            mode={publicView}
            email={email}
            password={password}
            busy={busy}
            error={dataError}
            onEmail={setEmail}
            onPassword={setPassword}
            onMode={navigatePublic}
            onSubmit={() => signIn(publicView === "signup" ? "signup" : "login")}
          />
        )}
        <ToastNotice notice={notice} />
      </>
    );
  }

  return (
    <div className="app" data-screen-label={`TTM ${view}`}>
      <a className="skip-link" href="#workspace-content" onClick={focusWorkspaceContent}>Skip to workspace content</a>
      <Sidebar
        view={view}
        setView={navigateWorkspace}
        mobileOpen={mobileNavOpen}
        onClose={() => closeMobileNav({ restoreFocus: true })}
        closeButtonRef={mobileCloseButtonRef}
        focusTarget={mobileNavFocusTarget}
        setFocusTarget={setMobileNavFocusTarget}
      />
      <main className="main" id="workspace-content" tabIndex={-1} aria-label={`${viewTitle(view)} workspace content`}>
        <Topbar
          view={view}
          account={currentAccount}
          health={health}
          dataMode={dataMode}
          busy={busy}
          onSignOut={signOut}
          onRefresh={() => loadAll()}
          loadingData={loadingData}
          mobileNavOpen={mobileNavOpen}
          mobileMenuButtonRef={mobileMenuButtonRef}
          onMobileNav={() => {
            if (mobileNavOpen) closeMobileNav({ restoreFocus: true });
            else setMobileNavOpen(true);
          }}
        />

        {dataError ? <NoticeBanner message={dataError} mode={dataMode} busy={loadingData} onRetry={() => loadAll()} /> : null}

        {view === "guide" ? (
          <SetupGuidePage
            account={currentAccount}
            dashboard={currentDashboard}
            usage={currentUsage}
            health={health || emptyHealth}
            onNavigate={navigateWorkspace}
          />
        ) : null}
        {view === "overview" ? (
          <OverviewPage
            account={currentAccount}
            dashboard={currentDashboard}
            usage={currentUsage}
            health={health || emptyHealth}
            auditEvents={auditEvents}
            chartData={chartData}
            proposals={proposals}
            tokenHistory={tokenHistory}
            busy={busy}
            loading={loadingData}
            scannerQuery={scannerQuery}
            tokenAddress={tokenAddress}
            amount={amount}
            onScannerQuery={setScannerQuery}
            onTokenAddress={setTokenAddress}
            onAmount={setAmount}
            onSimulate={runSimulation}
            onApprove={approveProposal}
            onRunScanner={runCopilotScan}
            onOpenSettings={() => navigateWorkspace("settings")}
            onOpenBot={() => navigateWorkspace("bot")}
          />
        ) : null}
        {view === "audit" ? <ProductionAuditPage health={health || emptyHealth} auditEvents={auditEvents} /> : null}
        {view === "paper" ? (
          <PaperPage
            account={currentAccount}
            health={health || emptyHealth}
            dashboard={currentDashboard}
            proposals={proposals}
            tokenHistory={tokenHistory}
            chartData={chartData}
            tokenAddress={tokenAddress}
            scannerQuery={scannerQuery}
            amount={amount}
            busy={busy}
            onTokenAddress={setTokenAddress}
            onScannerQuery={setScannerQuery}
            onAmount={setAmount}
            onSimulate={runSimulation}
            onRunScanner={runCopilotScan}
          />
        ) : null}
        {view === "bot" ? (
          <BotModesPage
            account={currentAccount}
            health={health || emptyHealth}
            activeApiKeys={activeApiKeys}
            mode={botMode}
            setMode={setBotMode}
            proposals={proposals}
            tokenHistory={tokenHistory}
            scannerRunEvidence={scannerRunEvidence}
            chatMessages={chatMessages}
            chatInput={chatInput}
            walletAddress={walletAddress}
            walletChainId={walletChainId}
            walletStatus={walletStatus}
            busy={busy}
            scannerQuery={scannerQuery}
            autopilotEnabled={botAutopilotEnabled}
            autopilotStatus={autopilotStatus}
            autopilotLastRunAt={autopilotLastRunAt}
            autopilotRuns={autopilotRuns}
            autopilotError={autopilotError}
            onScannerQuery={setScannerQuery}
            onAutopilotEnabled={setBotAutopilotEnabled}
            onChatInput={setChatInput}
            onSendAgentMessage={sendAgentMessage}
            onRunScanner={runCopilotScan}
            onConnectWallet={connectWallet}
            onApprove={approveProposal}
            onOpenSettings={() => navigateWorkspace("settings")}
          />
        ) : null}
        {view === "market" ? (
          <MarketPage
            health={health || emptyHealth}
            proposals={proposals}
            tokenHistory={tokenHistory}
            busy={busy}
            scannerQuery={scannerQuery}
            onScannerQuery={setScannerQuery}
            onRunScanner={runCopilotScan}
          />
        ) : null}
        {view === "pay" ? (
          <NanopaymentsPage
            artifacts={artifacts}
            selectedArtifactId={selectedArtifactId}
            selectedArtifact={selectedArtifact}
            activePayment={activePayment}
            nexusJob={nexusJob}
            activeArcProof={activeArcProof}
            busy={busy}
            onSelectedArtifact={setSelectedArtifactId}
            onCreatePayment={createPaymentIntent}
            onVerifyPayment={verifyPayment}
            onUnlockDelivery={unlockDelivery}
          />
        ) : null}
        {view === "keys" ? (
          <ApiKeysPage
            keys={keys}
            apiKey={apiKey}
            keyName={keyName}
            busy={busy}
            onApiKey={setApiKey}
            onKeyName={setKeyName}
            onCreate={createKey}
            onCopy={copyActiveKey}
            onRevoke={revokeKey}
          />
        ) : null}
        {view === "log" ? <AuditLogPage events={auditEvents} /> : null}
        {view === "docs" ? <DocsPage /> : null}
        {view === "settings" ? (
          <SettingsPage
            account={currentAccount}
            usage={currentUsage}
            health={health || emptyHealth}
            activeApiKeys={currentDashboard.active_api_keys}
            invoices={invoices}
            geminiApiKey={geminiApiKeyInput}
            geminiModel={geminiModel}
            onGeminiApiKey={setGeminiApiKeyInput}
            onGeminiModel={setGeminiModel}
            onSaveGemini={saveGeminiCredential}
            onDeleteGemini={deleteGeminiCredential}
            onCheckout={checkout}
            onSignOut={signOut}
            busy={busy || loadingData}
            checkoutPlan={checkoutPlan}
          />
        ) : null}
      </main>
      <ToastNotice notice={notice} />
    </div>
  );
}

function ToastNotice({ notice }: { notice: Notice }) {
  if (!notice) return null;

  return (
    <div className={`toast toast-${notice.type}`} role={notice.type === "error" ? "alert" : "status"} aria-live={notice.type === "error" ? "assertive" : "polite"}>
      <span className="sr-only">{notice.type === "error" ? "Erro" : "Sucesso"}: </span>
      {notice.text}
    </div>
  );
}

function LandingPage({
  onLogin,
  onSignup,
  onLegal,
}: {
  onLogin: () => void;
  onSignup: () => void;
  onLegal: (view: PublicView) => void;
}) {
  const goTop = () => window.scrollTo({ top: 0, behavior: "smooth" });
  const logos = ["circle", "solana", "helius", "goplus", "tokensniffer", "binance", "postgres"];
  const marqueeLogos = [...logos, ...logos];
  const modes = [
    {
      kicker: "01 - MODE 1",
      title: "Paper/Testnet autonomo",
      body: "O scanner le dados reais, filtra risco e executa apenas simulacoes no banco com slippage, gas e tax de contrato.",
      tone: "brand",
      checks: ["Sem chave privada", "Walk-forward backtest", "Monte-carlo de P&L esperado"],
    },
    {
      kicker: "02 - MODE 2",
      title: "Human-in-the-loop",
      body: "O bot acha o trade, calcula tamanho e faz auditoria. O usuario aprova no painel antes de qualquer acao sensivel.",
      tone: "warn",
      checks: ["Aprovacao com timeout", "Stop-loss apos simulacao", "Audit log de cada decisao"],
    },
    {
      kicker: "03 - MODE 3",
      title: "Real autonomo bloqueado",
      body: "A automacao 24/7 so aparece quando hard-caps, auditoria e historico de paper trading autorizarem o proximo modo.",
      tone: "err",
      checks: ["Hard-cap diario no banco", "Explanation trace", "Kill switch global"],
    },
  ];
  const pricing = [
    {
      name: "Free",
      price: "$0",
      description: "Pra estudar paper trading sem compromisso.",
      cta: "Comecar gratis",
      featured: false,
      included: ["1.000 simulate calls/mes", "10 backtests/mes", "1 API key", "Mode 1 paper"],
      excluded: ["Mode 2 copilot", "Mode 3 real autonomy"],
    },
    {
      name: "Pro",
      price: "$99",
      description: "Pra dev serio rodando uma ou mais estrategias em copilot.",
      cta: "Comecar com Pro",
      featured: true,
      included: ["100.000 simulate calls/mes", "500 backtests/mes", "5 API keys", "Mode 2 copilot"],
      excluded: [],
    },
    {
      name: "Scale",
      price: "$499",
      description: "Pra time com historico, RBAC e gates de producao mais rigidos.",
      cta: "Falar com vendas",
      featured: false,
      included: ["2M simulate calls/mes", "Backtests ilimitados", "Time multi-membro", "Mode 3 sob auditoria", "SLA e suporte dedicado"],
      excluded: [],
    },
  ];
  const faqs = [
    {
      q: "Vou perder dinheiro testando?",
      a: [
        "Zero risco financeiro. Enquanto voce testa, nenhum centavo vai para mercado. O bot opera com saldo simulado usando precos reais.",
        "Voce descobre se a estrategia funciona antes de arriscar dinheiro real.",
      ],
    },
    {
      q: "Eu preciso saber programar?",
      a: [
        "Nao. Voce pode escolher uma estrategia pronta, ajustar parametros e acompanhar o resultado no painel.",
        "Quem quiser customizar mais fundo tambem tem SDK Python e TypeScript.",
      ],
    },
    {
      q: "Em quanto tempo eu vejo resultado?",
      a: ["O workspace abre logo depois do cadastro. O historico estatistico depende do ritmo da estrategia, normalmente entre 1 e 4 semanas."],
    },
    {
      q: "Como o bot evita token golpe?",
      a: ["Antes de qualquer simulacao, o token passa por checagens de honeypot, tax de venda, liquidez, mint infinito e outras red flags."],
    },
    {
      q: "Quando posso operar com dinheiro real?",
      a: ["So quando o historico provar que vale. O produto mantem mainnet hard-blocked ate os gates de auditoria e risco autorizarem."],
    },
    {
      q: "Posso testar gratis de verdade?",
      a: ["Sim. O free tier nao exige cartao e continua disponivel dentro da quota mensal do plano gratuito."],
    },
  ];

  return (
    <div className="marketing-shell">
      <nav className="marketing-nav">
        <button className="brand-row marketing-brand" onClick={goTop} aria-label="TTM home">
          <div className="brand-mark" />
          <div className="col">
            <span className="brand-name">TTM</span>
            <span className="brand-sub">Paper Trading SaaS</span>
          </div>
        </button>
        <div className="marketing-links" aria-label="Product sections">
          <a href="#modes">Como funciona</a>
          <a href="#pricing">Precos</a>
          <a href="#faq">FAQ</a>
        </div>
        <div className="marketing-actions">
          <button className="btn ghost" onClick={onLogin}>Entrar</button>
          <button className="btn primary" onClick={onSignup}>Comecar gratis</button>
        </div>
      </nav>

      <main>
        <section className="marketing-hero">
          <div className="marketing-container hero-copy">
            <span className="marketing-eyebrow"><span className="dot" /> Free tier ativo - 1.000 chamadas gratis - sem cartao</span>
            <h1>
              Paper trading
              <span>autonomo</span>
              para bots cripto.
            </h1>
            <p>
              Scanner autonomo, auditoria de tokens, backtest realista e paper execution com fees, slippage e tax de contrato.
              Mode 1 roda sem dinheiro real, Mode 2 entra em aprovacao humana e Mode 3 fica bloqueado.
            </p>
            <div className="hero-actions">
              <button className="btn primary hero-btn" onClick={onSignup}>
                Comecar gratis
                <ArrowRight size={16} />
              </button>
              <button className="btn hero-btn" onClick={onLogin}>Entrar no dashboard</button>
            </div>
            <div className="hero-proof">
              <span><Check size={14} /> Setup em 2 minutos</span>
              <span><Check size={14} /> Cancele quando quiser</span>
              <span><Check size={14} /> +200 traders ativos</span>
            </div>
          </div>

          <div className="marketing-container">
            <div className="terminal-preview" aria-label="TTM SDK preview">
              <div className="terminal-bar">
                <span className="red" />
                <span className="amber" />
                <span className="green" />
                <strong>~/lab/ttm - paper-trade.py - arc-testnet</strong>
              </div>
              <div className="terminal-grid">
                <div>
                  <p className="muted mono"># 1. Audit gate</p>
                  <p className="mono command">ttm audit scan "0x9876...4321"</p>
                  <p className="mono ok">score: 96/100</p>
                  <p className="mono ok">liquidity_locked: true</p>
                  <p className="mono ok">honeypot: no</p>
                  <p className="muted mono spacer"># 2. Pay-per-call resource</p>
                  <p className="mono command">ttm money trace "$BONK" --limit=50</p>
                  <p className="mono warn">HTTP 402 PAYMENT-REQUIRED</p>
                  <p className="mono ok">settled 0.0025 USDC - 142ms</p>
                  <p className="muted mono spacer"># 3. Paper trade</p>
                  <p className="mono command">ttm simulate buy BONK/SOL --size=$10</p>
                  <p className="mono ok">fill 0.00002145 - slip 0.42%</p>
                  <p className="mono ok">tax buy 5% - fee 0.30%</p>
                </div>
                <div>
                  <p className="muted mono">// Same flow, TypeScript SDK</p>
                  <p className="mono"><span className="code-key">import</span> {"{ TTM }"} <span className="code-key">from</span> "@ttm/sdk"</p>
                  <p className="mono">{"const ttm = new TTM({ apiKey })"}</p>
                  <p className="mono">const audit = await ttm.audit.scan("0x9876...4321")</p>
                  <p className="mono">if (audit.score &gt;= 90) {"{"}</p>
                  <p className="mono">{"  await ttm.simulate.order({ symbol: 'BONK/SOL', side: 'buy' })"}</p>
                  <p className="mono">{"}"}</p>
                  <p className="mono ok spacer">mainnet execution remains hard-blocked</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="marketing-logos">
          <div className="marketing-container">
            <div className="marketing-proof-head">
              <div className="marketing-proof-eyebrow"><span className="dot" /> A stack que os fundos usam, agora no seu terminal</div>
              <h2>Os mesmos rails de quem move bilhoes em on-chain.</h2>
              <p>
                Circle, Solana, Helius, GoPlus e TokenSniffer ficam abstraidos no mesmo SDK. Crie uma conta gratis e acompanhe tudo por workspace.
              </p>
            </div>
          </div>
          <div className="marketing-marquee" aria-hidden="true">
            <div className="marketing-marquee-track">
              {marqueeLogos.map((logo, index) => (
                <span className="marketing-marquee-item" key={`${logo}-${index}`}>
                  {logo}
                  <i />
                </span>
              ))}
            </div>
          </div>
        </section>

        <section className="marketing-section" id="modes">
          <div className="marketing-container">
            <MarketingSectionHead eyebrow="Tres modos - um SDK" title="Comece simulando. Promova quando estiver pronto." body="O mesmo bot roda em tres modos sem trocar codigo. O dashboard autenticado mostra apenas dados do usuario logado." />
            <div className="marketing-pillar-grid">
              {modes.map((mode) => (
                <article className={`marketing-pillar marketing-pillar-${mode.tone}`} key={mode.title}>
                  <span>{mode.kicker}</span>
                  <h3>{mode.title}</h3>
                  <p>{mode.body}</p>
                  <ul>
                    {mode.checks.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="marketing-section" id="product">
          <div className="marketing-container">
            <div className="marketing-feature">
              <div className="marketing-feature-art">
                <div className="audit-preview">
                  <AuditRing score={82} />
                  <div>
                    <span className="chip brand">B+ production-track</span>
                    <strong>Production Audit</strong>
                    <small>8 pilares - 33 controles - 4 critical gaps</small>
                  </div>
                </div>
                <div className="marketing-score-grid">
                  <ScoreTile label="SaaS core" value="95%" tone="brand" />
                  <ScoreTile label="Paper ledger" value="88%" tone="brand" />
                  <ScoreTile label="Agent queue" value="78%" tone="brand" />
                  <ScoreTile label="Security" value="80%" tone="brand" />
                </div>
              </div>
              <div>
                <span className="section-eyebrow">Auditoria continua</span>
                <h3>Saiba exatamente o que falta para ir a producao.</h3>
                <p>
                  Cada deploy roda auditoria de SaaS core, ledger de paper, scanner, bot modes, seguranca, observabilidade, billing e infra.
                  Blocker critico mantem mainnet hard-blocked.
                </p>
                <ul className="marketing-feature-list">
                  <MarketingCheck>33 controles versionados no repo</MarketingCheck>
                  <MarketingCheck>Roadmap auto-priorizado por impacto</MarketingCheck>
                  <MarketingCheck>Blocker critico trava execucao real</MarketingCheck>
                </ul>
              </div>
            </div>

            <div className="marketing-feature reverse" id="approvals">
              <div className="marketing-feature-art">
                <div className="payment-step-row">
                  <span className="active">01 SCAN</span>
                  <span>02 SCORE</span>
                  <span>03 APPROVE</span>
                </div>
                <pre className="code">{`{
  "mode": "human_in_the_loop",
  "token": "0xSAFE",
  "score": 78,
  "action": "queue_for_approval"
}`}</pre>
              </div>
              <div>
                <span className="section-eyebrow">Mode 2 - Human-in-the-loop</span>
                <h3>O agente encontra oportunidades; voce decide quando vira execucao real.</h3>
                <p>
                  A fila de aprovacao usa o mesmo scanner do paper auto, mas bloqueia a transacao ate existir aprovacao humana,
                  limite de gasto e auditoria de risco.
                </p>
                <ul className="marketing-feature-list">
                  <MarketingCheck>Score de liquidez, momentum e risco antes da proposta</MarketingCheck>
                  <MarketingCheck>Execucao real bloqueada por padrao</MarketingCheck>
                  <MarketingCheck>Historico de aprovacoes auditavel por organizacao</MarketingCheck>
                </ul>
              </div>
            </div>

            <div className="marketing-feature">
              <div className="marketing-feature-art">
                <div className="paper-chart-head">
                  <div>
                    <strong>social-momentum - BONK/SOL - 1m</strong>
                    <small>240 bars - 12 folds</small>
                  </div>
                  <div className="paper-stats">
                    <span><small>SHARPE</small><strong>1.84</strong></span>
                    <span><small>MAX DD</small><strong className="neg">-18%</strong></span>
                    <span><small>CAGR</small><strong className="pos">+62%</strong></span>
                  </div>
                </div>
                <svg className="marketing-chart" viewBox="0 0 600 200" aria-hidden="true">
                  <defs>
                    <linearGradient id="marketingChartFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="#5BE9B9" stopOpacity="0.38" />
                      <stop offset="100%" stopColor="#5BE9B9" stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  <path d="M 0 180 L 30 175 L 60 168 L 90 178 L 120 160 L 150 145 L 180 152 L 210 138 L 240 125 L 270 112 L 300 105 L 330 88 L 360 92 L 390 78 L 420 65 L 450 70 L 480 58 L 510 50 L 540 38 L 570 30 L 600 20 L 600 200 L 0 200 Z" fill="url(#marketingChartFill)" />
                  <path d="M 0 180 L 30 175 L 60 168 L 90 178 L 120 160 L 150 145 L 180 152 L 210 138 L 240 125 L 270 112 L 300 105 L 330 88 L 360 92 L 390 78 L 420 65 L 450 70 L 480 58 L 510 50 L 540 38 L 570 30 L 600 20" fill="none" stroke="#5BE9B9" strokeWidth="2" />
                </svg>
              </div>
              <div>
                <span className="section-eyebrow">Paper trading serio</span>
                <h3>Backtest com fee, slippage, gas e tax de contrato.</h3>
                <p>
                  Memecoin com tax na compra e venda entra no P&L. Walk-forward por padrao, simulacao sem chave privada e sem acesso anonimo ao dashboard.
                </p>
                <ul className="marketing-feature-list">
                  <MarketingCheck>12 folds de walk-forward por estrategia</MarketingCheck>
                  <MarketingCheck>Toggles de fee, slip, tax e MEV</MarketingCheck>
                  <MarketingCheck>Performance agregada por estrategia e simbolo</MarketingCheck>
                </ul>
              </div>
            </div>
          </div>
        </section>

        <section className="marketing-section" id="pricing">
          <div className="marketing-container">
            <MarketingSectionHead eyebrow="Precos" title="Comece gratis. Pague pelo uso." body="Quotas mensais resetam dia 1. Excedentes e planos pagos ficam no workspace autenticado." />
            <div className="marketing-pricing-grid">
              {pricing.map((plan) => (
                <article className={`marketing-price ${plan.featured ? "featured" : ""}`} key={plan.name}>
                  {plan.featured ? <div className="marketing-price-tag">MAIS POPULAR</div> : null}
                  <div className="marketing-price-name">{plan.name}</div>
                  <div className="marketing-price-amt">{plan.price}<span>/mes</span></div>
                  <p>{plan.description}</p>
                  <ul>
                    {plan.included.map((item) => (
                      <MarketingCheck key={item}>{item}</MarketingCheck>
                    ))}
                    {plan.excluded.map((item) => (
                      <li className="muted" key={item}><X size={13} /> {item}</li>
                    ))}
                  </ul>
                  <button className={`btn ${plan.featured ? "primary" : ""} marketing-price-btn`} onClick={onSignup}>
                    {plan.cta}
                  </button>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="marketing-section" id="faq">
          <div className="marketing-container">
            <MarketingSectionHead eyebrow="FAQ" title="Tudo que voce precisa saber antes de comecar." body="Resposta direta, sem letra miuda. Se sobrar duvida depois disso, crie conta e veja o fluxo real." />
            <div className="marketing-faq">
              {faqs.map((item, index) => (
                <details className="marketing-faq-item" open={index === 0} key={item.q}>
                  <summary>
                    <span>{item.q}</span>
                    <i aria-hidden="true" />
                  </summary>
                  <div>
                    {item.a.map((answer) => (
                      <p key={answer}>{answer}</p>
                    ))}
                  </div>
                </details>
              ))}
            </div>
            <div className="marketing-faq-cta">
              <p>Mais facil ver funcionando do que ler sobre.</p>
              <button className="btn primary" onClick={onSignup}>Criar conta gratis <ArrowRight size={14} /></button>
            </div>
          </div>
        </section>

        <section className="marketing-container marketing-final-cta">
          <div className="marketing-cta-banner">
            <span className="section-eyebrow">Pronto?</span>
            <h2>Sua proxima estrategia merece um lab decente.</h2>
            <p>Free tier sem cartao. Saia em minutos com seu primeiro paper trade rodando.</p>
            <div className="hero-actions">
              <button className="btn primary hero-btn" onClick={onSignup}>Criar conta gratis <ArrowRight size={15} /></button>
              <button className="btn hero-btn" onClick={onLogin}>Ja tenho conta</button>
            </div>
          </div>
        </section>

        <footer className="marketing-footer">
          <div className="marketing-container">
            <div className="marketing-footer-cta">
              <div>
                <h3>Pare de ler. Comece a testar.</h3>
                <p>Conta gratis em 30 segundos. Sem cartao. Cancele quando quiser.</p>
              </div>
              <div className="marketing-footer-actions">
                <button className="btn primary" onClick={onSignup}>Criar conta gratis <ArrowRight size={14} /></button>
                <button className="btn ghost" onClick={onLogin}>Ja tenho conta</button>
              </div>
            </div>
            <div className="marketing-footer-bottom">
              <div className="brand-row">
                <div className="brand-mark" />
                <div className="col">
                  <span className="brand-name">TTM</span>
                  <span className="brand-sub">Paper Trading SaaS</span>
                </div>
              </div>
              <div className="marketing-legal-links" aria-label="Legal links">
                <button type="button" onClick={() => onLegal("legal")}>Legal</button>
                <button type="button" onClick={() => onLegal("terms")}>Termos</button>
                <button type="button" onClick={() => onLegal("privacy")}>Privacidade</button>
                <button type="button" onClick={() => onLegal("risk")}>Riscos</button>
              </div>
              <span>2026 TTM - Paper-only by design - Mainnet hard-blocked</span>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}

type LegalContent = {
  eyebrow: string;
  title: string;
  intro: string;
  sections: Array<{ title: string; body: string }>;
};

const legalContent: Record<Exclude<PublicView, "landing" | "login" | "signup">, LegalContent> = {
  legal: {
    eyebrow: "Legal center",
    title: "Contratos publicos e consentimento LGPD.",
    intro: "Resumo operacional dos termos, privacidade e riscos do ToTheMoonTokens. Estes textos sao drafts para revisao juridica e nao substituem aconselhamento legal.",
    sections: [
      { title: "Termos", body: "O uso do TTM exige conta autenticada, finalidade permitida, respeito a quotas e aceite de que o produto opera paper-only por padrao." },
      { title: "Privacidade", body: "Tratamos dados de conta, uso, billing, auditoria e metadados de credenciais para operar o servico, seguranca, suporte e obrigacoes legais." },
      { title: "Riscos", body: "Simulacoes, copilot, dashboards e IA sao ferramentas informativas. Nao oferecemos aconselhamento financeiro, juridico, fiscal, broker, exchange ou custodia." },
    ],
  },
  terms: {
    eyebrow: "Terms of Service",
    title: "Termos de uso do TTM.",
    intro: "Ao criar conta ou usar o workspace, voce concorda com regras de conta, uso aceitavel, billing, disponibilidade e guardrails paper-only.",
    sections: [
      { title: "Conta e acesso", body: "Voce e responsavel por credenciais, usuarios autorizados, configuracoes de API e atividade feita no seu workspace autenticado." },
      { title: "Uso aceitavel", body: "Nao use o produto para fraude, abuso, engenharia reversa indevida, tentativa de burlar quotas, ataque a terceiros ou violacao de leis aplicaveis." },
      { title: "Paper-only", body: "Ordens mainnet e automacao autonoma ficam bloqueadas salvo autorizacao explicita por politica de produto, seguranca, juridico e runbooks operacionais." },
    ],
  },
  privacy: {
    eyebrow: "Privacy Policy",
    title: "Politica de privacidade e LGPD.",
    intro: "Usamos dados pessoais somente para operar, proteger, melhorar e dar suporte ao TTM, incluindo cumprimento de obrigacoes legais e contratuais.",
    sections: [
      { title: "Dados tratados", body: "Podemos tratar email, organizacao, plano, uso, chaves em formato seguro, eventos de auditoria, billing, telemetria tecnica e pedidos de suporte." },
      { title: "Base e finalidade", body: "O tratamento pode apoiar execucao de contrato, seguranca, prevencao de abuso, obrigacoes legais, melhoria do produto e consentimentos especificos." },
      { title: "Direitos LGPD", body: "Voce pode solicitar acesso, correcao, exclusao, portabilidade, informacao sobre compartilhamento, revisao e revogacao de consentimento quando aplicavel." },
    ],
  },
  risk: {
    eyebrow: "Risk Disclosure",
    title: "Divulgacao de riscos.",
    intro: "Mercados cripto, memecoins, APIs de dados e modelos de IA sao incertos. Resultados simulados nao garantem performance futura.",
    sections: [
      { title: "Sem recomendacao", body: "Nenhuma tela, bot, score, auditoria, backtest ou resposta de IA deve ser tratada como recomendacao de investimento ou ordem para negociar." },
      { title: "Dados e modelos", body: "Precos, liquidez, honeypot checks, taxas, provedores e modelos podem falhar, atrasar, omitir contexto ou produzir sinais incorretos." },
      { title: "Controle humano", body: "Revise toda decisao sensivel, mantenha limites conservadores e nao promova modos de automacao sem aprovacao formal e evidencia operacional." },
    ],
  },
};

function LegalPage({ view, onNavigate }: { view: "legal" | "privacy" | "terms" | "risk"; onNavigate: (view: PublicView) => void }) {
  const content = legalContent[view];
  return (
    <div className="marketing-shell legal-shell">
      <nav className="marketing-nav">
        <button className="brand-row marketing-brand" onClick={() => onNavigate("landing")} aria-label="TTM home">
          <div className="brand-mark" />
          <div className="col">
            <span className="brand-name">TTM</span>
            <span className="brand-sub">Paper Trading SaaS</span>
          </div>
        </button>
        <div className="marketing-links" aria-label="Legal pages">
          <button type="button" onClick={() => onNavigate("terms")}>Termos</button>
          <button type="button" onClick={() => onNavigate("privacy")}>Privacidade</button>
          <button type="button" onClick={() => onNavigate("risk")}>Riscos</button>
        </div>
        <div className="marketing-actions">
          <button className="btn ghost" onClick={() => onNavigate("login")}>Entrar</button>
          <button className="btn primary" onClick={() => onNavigate("signup")}>Comecar gratis</button>
        </div>
      </nav>
      <main className="marketing-container legal-main">
        <span className="marketing-eyebrow"><span className="dot" /> {content.eyebrow}</span>
        <h1>{content.title}</h1>
        <p className="lead">{content.intro}</p>
        <div className="legal-grid">
          {content.sections.map((section) => (
            <article className="card legal-card" key={section.title}>
              <CardHead title={section.title} sub="Public compliance surface" />
              <p>{section.body}</p>
            </article>
          ))}
        </div>
        <section className="card legal-notice">
          <CardHead title="Status juridico" sub="Attorney-review draft" badge="draft" />
          <p>
            Estes textos sao uma superficie publica de compliance baseada nos drafts em docs/legal. Publique somente depois de
            revisao juridica, definicao de contatos oficiais, prazos de retencao, subprocessadores e workflows regionais.
          </p>
        </section>
      </main>
    </div>
  );
}

function MarketingSectionHead({ eyebrow, title, body }: { eyebrow: string; title: string; body: string }) {
  return (
    <div className="marketing-section-head">
      <div className="section-eyebrow">{eyebrow}</div>
      <h2>{title}</h2>
      <p>{body}</p>
    </div>
  );
}

function MarketingCheck({ children }: { children: ReactNode }) {
  return (
    <li>
      <Check size={13} />
      {children}
    </li>
  );
}

function ScoreTile({ label, value, tone }: { label: string; value: string; tone: "brand" | "warn" }) {
  return (
    <div>
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function MarketingAuthPage({
  mode,
  email,
  password,
  busy,
  error,
  onEmail,
  onPassword,
  onMode,
  onSubmit,
}: {
  mode: "login" | "signup";
  email: string;
  password: string;
  busy: boolean;
  error: string | null;
  onEmail: (value: string) => void;
  onPassword: (value: string) => void;
  onMode: (mode: PublicView) => void;
  onSubmit: () => void;
}) {
  const isSignup = mode === "signup";
  return (
    <div className="auth-page">
      <section className="auth-left">
        <button className="brand-row marketing-brand" onClick={() => onMode("landing")} aria-label="Voltar para landing page">
          <div className="brand-mark" />
          <div className="col">
            <span className="brand-name">TTM</span>
            <span className="brand-sub">Paper Trading SaaS</span>
          </div>
        </button>

        <form
          className="auth-form-wrap"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          {isSignup ? <div className="signup-step-bar"><span /><span /><span /></div> : null}
          <h1>{isSignup ? "Criar conta gratis." : "Bem-vindo de volta."}</h1>
          <p className="lead">
            {isSignup
              ? "1.000 simulate calls/mes, sem cartao, paper trading desbloqueado em segundos."
              : "Entre para acessar seu workspace, API keys, paper ledger e lifecycle de pagamentos."}
          </p>

          {isSignup ? (
            <div className="field-row">
              <label>
                Nome completo
                <input placeholder="Seu nome" autoComplete="name" />
              </label>
              <label>
                Organizacao
                <input placeholder="sua-empresa" autoComplete="organization" />
              </label>
            </div>
          ) : null}

          <label>
            Email
            <input
              value={email}
              onChange={(event) => onEmail(event.target.value)}
              placeholder="synthetic-user-1@example.com"
              type="email"
              autoComplete="email"
              required
            />
          </label>
          <label>
            Senha
            <input
              value={password}
              onChange={(event) => onPassword(event.target.value)}
              placeholder="Password"
              type="password"
              autoComplete={isSignup ? "new-password" : "current-password"}
              minLength={12}
              required
            />
          </label>
          {isSignup ? (
            <label>
              Pra que voce vai usar o TTM?
              <select defaultValue="backtest">
                <option value="backtest">Backtest de estrategias de memecoin</option>
                <option value="copy">Bot de smart-money copy trading</option>
                <option value="autopilot">Scanner autonomo com paper trading</option>
                <option value="research">Pesquisa academica</option>
              </select>
            </label>
          ) : null}

          {error ? <div className="auth-error" role="alert">{error}</div> : null}

          <button type="submit" className="btn primary auth-submit" disabled={busy}>
            {isSignup ? "Criar conta + abrir dashboard" : "Entrar"}
            <ArrowRight size={15} />
          </button>

          <p className="auth-disclosure">
            TTM e laboratorio de paper trading. Ao criar conta, voce aceita os Termos, a Politica de Privacidade/LGPD e a Divulgacao de Riscos.
          </p>
          <p className="auth-footer">
            {isSignup ? "Ja tem conta? " : "Nao tem conta? "}
            <button type="button" onClick={() => onMode(isSignup ? "login" : "signup")}>
              {isSignup ? "Entrar" : "Criar conta gratis"}
            </button>
          </p>
        </form>
      </section>

      <aside className="auth-right">
        <div className="auth-quote">
          <span className="marketing-eyebrow"><span className="dot" /> paper-only by policy</span>
          <blockquote>
            {isSignup
              ? "Rode Mode 1 em paper hoje. Mode 3 real so depois de historico, flags e aprovacao."
              : "Seu dashboard mostra apenas dados do workspace autenticado, sem preview anonimo."}
          </blockquote>
          <div className="auth-feats">
            <FeatureCheck title="API keys com hash" body="Plaintext aparece uma unica vez e revogacao e auditada." />
            <FeatureCheck title="Quota por organizacao" body="Uso, chaves e auditoria sao carregados por JWT." />
            <FeatureCheck title="Bot modes com guardrails" body="Paper auto, aprovacoes humanas e real autonomo bloqueado por politica." />
          </div>
        </div>
      </aside>
    </div>
  );
}

function FeatureCheck({ title, body }: { title: string; body: string }) {
  return (
    <div className="auth-feat">
      <span><Check size={13} /></span>
      <div>
        <strong>{title}</strong>
        <small>{body}</small>
      </div>
    </div>
  );
}

function Sidebar({
  view,
  setView,
  mobileOpen,
  onClose,
  closeButtonRef,
  focusTarget,
  setFocusTarget,
}: {
  view: View;
  setView: (view: View) => void;
  mobileOpen: boolean;
  onClose: () => void;
  closeButtonRef: RefObject<HTMLButtonElement | null>;
  focusTarget: "close" | "active";
  setFocusTarget: (focusTarget: "close" | "active") => void;
}) {
  const firstNavItemRef = useRef<HTMLButtonElement | null>(null);
  const activeNavItemRef = useRef<HTMLButtonElement | null>(null);
  const sections: Array<{ title: string; items: Array<{ id: View; label: string; icon: ReactNode; badge?: string; live?: boolean }> }> = [
    {
      title: "Trade",
      items: [
        { id: "guide", label: "Guia de Setup", icon: <BookOpen size={16} /> },
        { id: "overview", label: "Overview", icon: <Gauge size={16} /> },
        { id: "audit", label: "Production Audit", icon: <Shield size={16} />, badge: "82%", live: true },
        { id: "paper", label: "Paper Trading", icon: <FileText size={16} /> },
        { id: "bot", label: "Bot Modes", icon: <Bot size={16} />, badge: "SIM" },
        { id: "market", label: "Market Data", icon: <LineChart size={16} /> },
      ],
    },
    {
      title: "Build",
      items: [
        { id: "keys", label: "API Keys", icon: <KeyRound size={16} /> },
        { id: "log", label: "Audit Log", icon: <Activity size={16} /> },
      ],
    },
    {
      title: "Account",
      items: [
        { id: "docs", label: "Docs & SDK", icon: <BookOpen size={16} /> },
        { id: "settings", label: "Settings", icon: <Settings size={16} /> },
      ],
    },
  ];

  useEffect(() => {
    if (!mobileOpen || focusTarget !== "active") return;
    (activeNavItemRef.current || firstNavItemRef.current)?.focus({ preventScroll: true });
  }, [mobileOpen, focusTarget, view]);

  return (
    <>
      <aside id="workspace-navigation" className={`sidebar ${mobileOpen ? "mobile-open" : ""}`} aria-label="Workspace navigation">
        <div className="brand-row">
          <div className="brand-mark" />
          <div className="col">
            <span className="brand-name">TTM</span>
            <span className="brand-sub">Paper Trading SaaS</span>
          </div>
          <button ref={closeButtonRef} className="btn ghost mobile-close" type="button" onClick={onClose} aria-label="Close navigation panel">
            <X size={16} />
          </button>
        </div>

        {sections.map((section) => (
          <div className="nav-section" key={section.title}>
            <div className="nav-section-title">{section.title}</div>
            {section.items.map((item) => (
              <button
                ref={(element) => {
                  if (section === sections[0] && item === section.items[0]) firstNavItemRef.current = element;
                  if (view === item.id) activeNavItemRef.current = element;
                }}
                type="button"
                className={`nav-item ${view === item.id ? "active" : ""}`}
                key={item.id}
                aria-current={view === item.id ? "page" : undefined}
                onClick={() => {
                  setView(item.id);
                  setFocusTarget("active");
                  onClose();
                }}
              >
                {item.icon}
                <span>{item.label}</span>
                {item.badge ? <span className={`badge ${item.live ? "live" : ""}`}>{item.badge}</span> : null}
              </button>
            ))}
          </div>
        ))}

        <div className="sidebar-footer">
          <div className="user-card">
            <div className="avatar">TT</div>
            <div className="col">
              <span className="user-name">TTM Workspace</span>
              <span className="user-org">paper trading only</span>
            </div>
          </div>
        </div>
      </aside>
      {mobileOpen ? <button className="mobile-scrim" type="button" aria-label="Close navigation overlay" onClick={onClose} /> : null}
    </>
  );
}

function Topbar({
  view,
  account,
  health,
  dataMode,
  busy,
  onSignOut,
  onRefresh,
  loadingData,
  mobileNavOpen,
  mobileMenuButtonRef,
  onMobileNav,
}: {
  view: View;
  account: Account;
  health: Health | null;
  dataMode: DataMode;
  busy: boolean;
  onSignOut: () => void;
  onRefresh: () => void;
  loadingData: boolean;
  mobileNavOpen: boolean;
  onMobileNav: () => void;
  mobileMenuButtonRef: RefObject<HTMLButtonElement | null>;
}) {
  const crumbs = ["TTM SaaS", navGroup(view), viewTitle(view)];
  const title = viewTitle(view);

  return (
    <header className="topbar">
      <button
        ref={mobileMenuButtonRef}
        className="btn ghost mobile-menu"
        type="button"
        onClick={onMobileNav}
        aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"}
        aria-controls="workspace-navigation"
        aria-expanded={mobileNavOpen}
      >
        <Menu size={16} />
      </button>
      <div className="mobile-page-title" aria-hidden="true">
        <span>{navGroup(view)}</span>
        <strong>{title}</strong>
      </div>
      <div className="crumbs">
        {crumbs.map((crumb, index) => (
          <span className={index === crumbs.length - 1 ? "crumb current" : "crumb"} key={crumb}>
            {index > 0 ? <span className="crumbs-sep">/</span> : null}
            {index === crumbs.length - 1 ? <strong>{crumb}</strong> : crumb}
          </span>
        ))}
      </div>
      <div className="topbar-right">
        <div className="search-chip" aria-hidden="true">
          <Search size={13} />
          <span>Search resources, trades, keys</span>
          <span className="shortcut">/</span>
        </div>
        <div className={`env-pill ${health?.orderSubmissionEnabled ? "warn" : ""}`}>
          <span className="dot" />
          {health?.orderSubmissionEnabled ? "Mainnet armed" : "Paper only"}
        </div>
        <button
          className="btn ghost icon-only"
          onClick={onRefresh}
          title={loadingData ? "Refreshing data" : "Refresh data"}
          aria-label={loadingData ? "Refreshing data" : "Refresh data"}
          aria-busy={loadingData || undefined}
          disabled={loadingData}
        >
          <RefreshCw size={15} />
        </button>
        <button
          className="btn ghost icon-only"
          title="Notifications coming soon"
          aria-label="Notifications coming soon"
          aria-disabled="true"
          disabled
        >
          <Bell size={15} />
        </button>
        <div className="top-user">
          <span>{account.organization}</span>
          <small>{dataMode === "live" ? account.email : "sync degraded"}</small>
          <button className="btn ghost icon-only" onClick={onSignOut} aria-label="Sign out" title="Sign out" disabled={busy}>
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </header>
  );
}

function SetupGuidePage({
  account,
  dashboard,
  usage,
  health,
  onNavigate,
}: {
  account: Account;
  dashboard: Dashboard;
  usage: Usage;
  health: Health;
  onNavigate: (view: View) => void;
}) {
  const geminiCredential = account.provider_credentials?.gemini;
  const geminiConfigured = Boolean(geminiCredential?.configured);
  const firstQuestionReady = geminiConfigured && health.ok;
  const healthLabel = health.ok ? "API online" : "API instavel";
  const firstPrompt = "Revise minha configuracao e sugira o primeiro paper trade seguro para testar hoje.";
  const [promptCopyStatus, setPromptCopyStatus] = useState("");
  const copyFirstPrompt = async () => {
    try {
      await navigator.clipboard.writeText(firstPrompt);
      setPromptCopyStatus("Prompt copiado. Cole no Bot Modes.");
    } catch {
      setPromptCopyStatus("Copie o prompt manualmente.");
    }
  };
  const steps: Array<{
    title: string;
    body: string;
    done: boolean;
    action: string;
    target: View;
  }> = [
    {
      title: "1. Conta criada",
      body: `Voce esta logado como ${account.email || "usuario"} no workspace autenticado.`,
      done: Boolean(account.org_id),
      action: "Ver conta",
      target: "settings",
    },
    {
      title: "2. Configurar Gemini API key",
      body: geminiConfigured
        ? `Gemini configurado${geminiCredential?.model ? ` com ${geminiCredential.model}` : ""}. A chave completa nao volta para o navegador.`
        : "Cole sua Gemini API key em Settings para o bot responder usando a sua conta.",
      done: geminiConfigured,
      action: geminiConfigured ? "Rotacionar key" : "Configurar Gemini",
      target: "settings",
    },
    {
      title: "3. Rodar scanner autonomo",
      body: geminiConfigured
        ? "Use Bot Modes para acionar Mode 1 paper auto ou Mode 2 com aprovacao humana."
        : "O bot fica pronto depois que a Gemini API key estiver configurada.",
      done: geminiConfigured,
      action: "Abrir Bot Modes",
      target: "bot",
    },
    {
      title: "4. Validar fila de propostas",
      body: firstQuestionReady
        ? "O chat explica a fila, mas o scanner e quem trabalha sem conversa manual."
        : "A primeira pergunta depende da Gemini API key salva e da API online.",
      done: firstQuestionReady,
      action: "Testar no bot",
      target: "bot",
    },
    {
      title: "5. Entender paper/manual",
      body: "Trading roda em paper e pagamentos seguem fluxo manual/teste. Mainnet e entregas autonomas continuam bloqueadas por politica.",
      done: true,
      action: "Ver modos",
      target: "paper",
    },
  ];
  const completed = steps.filter((step) => step.done).length;
  const nextStep = steps.find((step) => !step.done) || steps[steps.length - 1];
  const nextStepTitle = nextStep.title.replace(/^\d+\.\s*/, "");
  const progressValue = Math.round((completed / steps.length) * 100);
  const progressSummary = `${completed} de ${steps.length} passos concluidos. Proximo passo: ${nextStepTitle}.`;
  const readinessChecks = [
    { label: "Conta", ready: Boolean(account.org_id), detail: account.email || "login pendente" },
    { label: "Gemini", ready: geminiConfigured, detail: geminiConfigured ? geminiCredential?.key_hint || "key salva" : "key ausente" },
    { label: "Backend", ready: health.ok, detail: healthLabel },
    { label: "Modo", ready: !health.orderSubmissionEnabled, detail: health.orderSubmissionEnabled ? "mainnet armed" : "paper only" },
  ];
  const readyCount = readinessChecks.filter((check) => check.ready).length;
  const mode3Locked = !health.orderSubmissionEnabled;
  const mode3GateSummary = mode3Locked
    ? "Mode 3 real autonomy locked: org flags, human approval, hard daily limits, audit evidence, and production risk gates are still required."
    : "Mode 3 risk gate warning: backend reports order submission enabled; confirm production approvals before any real execution.";
  const nextActionLabel = geminiConfigured ? "Abrir Bot Modes" : "Ir para Settings";
  const nextStepDescription = nextStep.done
    ? "Setup essencial concluido. Revise os modos paper/manual antes de operar."
    : nextStep.body;
  const nextStepActionDescription = `Proximo passo: ${nextStepTitle}. ${nextStepDescription}`;
  const recommendedFocus = geminiConfigured
    ? "Agora rode o scanner ou peça ao bot para explicar a proxima proposta paper segura."
    : "Comece salvando a Gemini API key em Settings para liberar o bot da sua conta.";

  return (
    <div className="page">
      <PageHead
        title="Guia de Setup"
        subtitle="Siga estes passos para deixar sua conta pronta e entender o que ja esta rodando."
        actions={
          <>
            <button className="btn" onClick={() => onNavigate("docs")}>
              <BookOpen size={13} />
              Abrir docs
            </button>
            <button
              className="btn primary"
              onClick={() => onNavigate(nextStep.target)}
              aria-describedby="setup-next-step-action"
              aria-label={`Abrir proximo passo: ${nextStepTitle}`}
            >
              <ArrowRight size={13} />
              Proximo passo
            </button>
          </>
        }
      />

      <p id="setup-next-step-action" className="sr-only">{nextStepActionDescription}</p>

      <section className="setup-handoff" aria-labelledby="setup-handoff-title">
        <div>
          <span>Foco agora</span>
          <h2 id="setup-handoff-title">{nextStepTitle}</h2>
          <p>{recommendedFocus}</p>
        </div>
        <div className="setup-handoff-meta" aria-label="Resumo rapido do onboarding">
          <span>{progressValue}% pronto</span>
          <span>{readyCount}/{readinessChecks.length} checks OK</span>
          <span>{health.orderSubmissionEnabled ? "mainnet armed" : "paper only"}</span>
        </div>
        <button
          className="btn primary"
          onClick={() => onNavigate(nextStep.target)}
          aria-describedby="setup-next-step-action"
          aria-label={`Continuar para ${nextStepTitle}`}
        >
          <ArrowRight size={13} /> Continuar
        </button>
      </section>

      <div className="setup-grid">
        <div className="card setup-progress">
          <CardHead title="Checklist da conta" sub={`${completed}/${steps.length} passos concluidos`} badge={geminiConfigured ? "bot ready" : "setup"} />
          <p className="setup-progress-caption" id="setup-progress-summary">{progressSummary}</p>
          <div
            className="setup-meter"
            role="progressbar"
            aria-label="Progresso do setup da conta"
            aria-describedby="setup-progress-summary"
            aria-valuemin={0}
            aria-valuemax={steps.length}
            aria-valuenow={completed}
            aria-valuetext={progressSummary}
          >
            <span style={{ width: `${progressValue}%` }} />
          </div>
          <ol className="setup-steps" aria-label="Passos do onboarding">
            {steps.map((step, index) => {
              const isCurrentStep = step.title === nextStep.title && !step.done;
              const statusText = step.done ? "concluido" : isCurrentStep ? "proximo passo" : "pendente";
              return (
                <li className={`setup-step ${step.done ? "done" : ""} ${isCurrentStep ? "current" : ""}`} key={step.title} aria-current={isCurrentStep ? "step" : undefined}>
                  <span className="setup-step-icon" aria-hidden="true">
                    {step.done ? <Check size={14} /> : <ArrowRight size={14} />}
                  </span>
                  <div>
                    <strong>{step.title}</strong>
                    <span className="sr-only">Status do passo {index + 1}: {statusText}. </span>
                    <span>{step.body}</span>
                  </div>
                  <button className={step.done ? "btn sm" : "btn sm primary"} onClick={() => onNavigate(step.target)} aria-label={`${step.action}: ${step.title}`}>
                    {step.action}
                  </button>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="card setup-summary" aria-label="Resumo do setup da conta">
          <CardHead title="Estado atual" sub="O que esta ativo agora" />
          <Detail label="Runtime" value={health.orderSubmissionEnabled ? "mainnet armed" : "paper only"} />
          <Detail label="Mode 3" value={mode3Locked ? "locked" : "needs review"} />
          <Detail label="Backend" value={healthLabel} />
          <Detail label="Gemini" value={geminiConfigured ? "configured" : "missing"} />
          <Detail label="Proximo passo" value={nextStepTitle} />
          <Detail label="Requests mes" value={String(usage.requests_month || 0)} />
          <div className={`setup-mode3-lock ${mode3Locked ? "locked" : "warn"}`} aria-label="Estado bloqueado do Mode 3">
            <div>
              <span className="setup-mode3-icon" aria-hidden="true">
                {mode3Locked ? <Lock size={14} /> : <AlertTriangle size={14} />}
              </span>
              <div>
                <strong>{mode3Locked ? "Mode 3 autonomia real bloqueada" : "Mode 3 exige revisao"}</strong>
                <p>{mode3GateSummary}</p>
              </div>
            </div>
            <button className="btn sm" onClick={() => onNavigate("bot")} aria-label="Abrir Bot Modes para revisar Mode 3 bloqueado">
              Revisar Bot Modes
            </button>
          </div>
          <div className="setup-next-action" aria-label="Proxima acao recomendada">
            <span>Proxima acao recomendada</span>
            <strong>{nextStepTitle}</strong>
            <p>{nextStepDescription}</p>
            <button className="btn sm primary" onClick={() => onNavigate(nextStep.target)} aria-label={`Abrir ${nextStepTitle} como proxima acao`}>
              <ArrowRight size={12} /> Abrir passo
            </button>
          </div>
          <div className="setup-readiness" aria-label="Resumo de prontidao do onboarding">
            <div>
              <span>Prontidao</span>
              <strong>{readyCount}/{readinessChecks.length} checks OK</strong>
            </div>
            <div className="setup-readiness-list">
              {readinessChecks.map((check) => (
                <span className={check.ready ? "ok" : "warn"} key={check.label}>
                  {check.ready ? <Check size={12} /> : <AlertTriangle size={12} />}
                  {check.label}: {check.detail}
                </span>
              ))}
            </div>
          </div>
          <div className="setup-note">
            Trading real e pagamentos autonomos continuam bloqueados por politica de backend. O primeiro uso recomendado e salvar a Gemini key e testar o bot em modo paper.
          </div>
          <div className="first-prompt-card" aria-label="Primeira pergunta sugerida para o bot">
            <span>Primeira pergunta sugerida</span>
            <strong>{firstPrompt}</strong>
            <div className="first-prompt-actions">
              <button className="btn sm" onClick={() => void copyFirstPrompt()} aria-describedby="first-prompt-copy-status">
                <Copy size={12} /> Copiar prompt
              </button>
              <button
                className="btn sm primary"
                onClick={() => onNavigate(geminiConfigured ? "bot" : "settings")}
                aria-label={`${nextActionLabel} para completar o onboarding`}
              >
                <ArrowRight size={12} /> {nextActionLabel}
              </button>
            </div>
            <p id="first-prompt-copy-status" className="muted small" role="status" aria-live="polite">
              {promptCopyStatus || "Copie e cole no Bot Modes depois de salvar a Gemini key."}
            </p>
          </div>
        </div>
      </div>

      <Divider label="Caminho recomendado" />
      <div className="three-grid">
        <GuideAction
          icon={<KeyRound size={16} />}
          title="Salvar Gemini key"
          body="Sem isso, o bot nao usa sua credencial pessoal."
          action="Ir para Settings"
          onClick={() => onNavigate("settings")}
        />
        <GuideAction
          icon={<Bot size={16} />}
          title="Testar o bot"
          body="Confira simulador, copilot e aprovacoes manuais."
          action="Abrir Bot Modes"
          onClick={() => onNavigate("bot")}
        />
        <GuideAction
          icon={<Play size={16} />}
          title="Rodar paper trade"
          body="Execute uma simulacao sem ordem real ou mainnet."
          action="Abrir Paper Trading"
          onClick={() => onNavigate("paper")}
        />
      </div>
    </div>
  );
}

function GuideAction({
  icon,
  title,
  body,
  action,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  action: string;
  onClick: () => void;
}) {
  return (
    <div className="card guide-action">
      <span className="guide-action-icon">{icon}</span>
      <strong>{title}</strong>
      <span>{body}</span>
      <button className="btn full" onClick={onClick}>
        {action}
      </button>
    </div>
  );
}

function OverviewPage({
  account,
  dashboard,
  usage,
  health,
  auditEvents,
  chartData,
  proposals,
  tokenHistory,
  busy,
  loading,
  scannerQuery,
  tokenAddress,
  amount,
  onScannerQuery,
  onTokenAddress,
  onAmount,
  onSimulate,
  onApprove,
  onRunScanner,
  onOpenSettings,
  onOpenBot,
}: {
  account: Account;
  dashboard: Dashboard;
  usage: Usage;
  health: Health;
  auditEvents: AuditEvent[];
  chartData: NonNullable<Dashboard["last_30_days_chart_points"]>;
  proposals: CopilotProposal[];
  tokenHistory: TokenDiscoveryHistoryRecord[];
  busy: boolean;
  loading: boolean;
  scannerQuery: string;
  tokenAddress: string;
  amount: number;
  onScannerQuery: (value: string) => void;
  onTokenAddress: (value: string) => void;
  onAmount: (value: number) => void;
  onSimulate: () => void;
  onApprove: (id: number) => void;
  onRunScanner: (mode?: ScannerExecutionMode) => void;
  onOpenSettings: () => void;
  onOpenBot: () => void;
}) {
  const totalVolume = dashboard.total_volume_usd ?? dashboard.simulated_volume_usd ?? 0;
  const chartVolumes = chartData.map((point) => point.volume_usd);
  const chartTrades = chartData.map((point) => point.trades);
  const hasVolumeHistory = chartVolumes.some((value) => value > 0);
  const hasRequestHistory = usage.requests_month > 0 || usage.requests_today > 0;
  const tradeRows = recentPaperRows(proposals);
  const pendingProposals = proposals.filter((proposal) => proposal.status === "pending").length;
  const executedProposals = proposals.filter((proposal) => proposal.status === "executed").length;
  const blockedDiscoveries = tokenHistory.filter((record) => record.recommendation === "skip").length;
  const hasPaperActivity = hasVolumeHistory || (dashboard.total_simulated_trades || 0) > 0 || tradeRows.length > 0;
  const hasWorkspaceActivity = hasPaperActivity || hasRequestHistory || (dashboard.active_api_keys || 0) > 0 || proposals.length > 0;
  const volumeSpark = hasVolumeHistory ? sparkSeries(chartVolumes, totalVolume) : undefined;
  const auditControls = [health.ok, health.mode === "paper", !health.orderSubmissionEnabled, auditEvents.length > 0];
  const auditScore = Math.round((auditControls.filter(Boolean).length / auditControls.length) * 100);
  const geminiCredential = account.provider_credentials?.gemini;
  const geminiConfigured = Boolean(geminiCredential?.configured);
  const firstQuestionReady = geminiConfigured;

  return (
    <div className="page">
      <PageHead
        title="Overview"
        subtitle="Autonomous paper scanner, proposal queue, and production guardrails in one cockpit."
        actions={
          <>
            <button className="btn">
              <Download size={13} />
              Export ledger
            </button>
            <button className="btn primary" onClick={onSimulate} disabled={busy}>
              <Plus size={13} />
              New simulation
            </button>
          </>
        }
      />

      <div className="kpi-grid">
        <KPI label="Agent Signals" value={compactNumber(proposals.length)} delta={`${pendingProposals} pending, ${executedProposals} paper executed`} spark={sparkSeries([pendingProposals, proposals.length, executedProposals])} emptyLabel="No scanner proposals yet" />
        <KPI label="Tokens Analyzed" value={compactNumber(tokenHistory.length)} delta={`${blockedDiscoveries} blocked by audit`} spark={sparkSeries(tokenHistory.slice(0, 12).map((record) => proposalScorePct(record.score || 0)), tokenHistory.length)} sparkColor="var(--plasma)" emptyLabel="No token analysis yet" />
        <KPI label="Volume" value={money(totalVolume)} delta={`${dashboard.total_simulated_trades || 0} paper trades`} spark={volumeSpark} sparkColor="var(--signal)" emptyLabel="No simulated volume yet" />
        <KPI label="Audit Score" value={String(auditScore)} unit="/100" delta={health.orderSubmissionEnabled ? "mainnet orders armed" : "mainnet orders blocked"} spark={sparkSeries([...auditControls.map((passing) => (passing ? 100 : 0)), ...chartTrades])} />
      </div>

      <OnboardingGuide
        geminiConfigured={geminiConfigured}
        geminiModel={geminiCredential?.model}
        firstQuestionReady={firstQuestionReady}
        onOpenSettings={onOpenSettings}
        onOpenBot={onOpenBot}
      />

      <div className="dashboard-grid">
        <div className="card">
          <CardHead title="Equity curve - paper" sub={`${money(dashboard.simulated_volume_usd)} simulated volume`} />
          {hasPaperActivity ? (
            <EquityChart points={chartData.map((point) => point.volume_usd)} loading={loading} />
          ) : (
            <DashboardEmptyState
              icon={<LineChart size={18} />}
              title="No paper trading history yet"
              body="Run your first paper simulation to populate the equity curve, trade ledger, and simulated volume metrics for this workspace."
              action={<button className="btn sm primary" onClick={onSimulate} disabled={busy}>Run first simulation</button>}
            />
          )}
        </div>
        <div className="card">
          <CardHead title="Workspace mix" sub={`${dashboard.active_api_keys} active API keys`} badge={`${proposals.length} proposals`} />
          {hasWorkspaceActivity ? (
            <StrategyMix dashboard={dashboard} usage={usage} proposals={proposals} />
          ) : (
            <DashboardEmptyState
              icon={<KeyRound size={18} />}
              title="No agent activity yet"
              body="Create a scoped API key, connect Gemini, or run the scanner so token proposals and paper execution appear here."
              action={<button className="btn sm" onClick={onOpenSettings}>Open Settings</button>}
            />
          )}
        </div>
      </div>

      <AutonomousAgentPanel
        proposals={proposals}
        tokenHistory={tokenHistory}
        health={health}
        geminiConfigured={geminiConfigured}
        busy={busy}
        scannerQuery={scannerQuery}
        onScannerQuery={onScannerQuery}
        onRunScanner={onRunScanner}
        onOpenSettings={onOpenSettings}
        onOpenBot={onOpenBot}
      />

      <div className="work-grid">
        <SimulationCard tokenAddress={tokenAddress} amount={amount} busy={busy} onTokenAddress={onTokenAddress} onAmount={onAmount} onSimulate={onSimulate} />
        <CopilotCard proposals={proposals} busy={busy} onApprove={onApprove} />
      </div>

      <Divider label="Recent paper trades" />
      <div className="card flush">
        <table className="table">
          <thead>
            <tr>
              <th>Pair</th>
              <th>Side</th>
              <th>Size</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>P&L</th>
              <th>Strategy</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {tradeRows.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-cell">No agent proposals or paper executions yet. Run the scanner to populate this tenant-scoped ledger.</td>
              </tr>
            ) : tradeRows.map((trade) => (
              <tr key={`${trade.sym}-${trade.pnl}`}>
                <td className="bold">{trade.sym}</td>
                <td><span className="chip brand">{trade.side}</span></td>
                <td className="num">{trade.size}</td>
                <td className="num">{trade.entry}</td>
                <td className="num">{trade.exit}</td>
                <td className="num" style={{ color: trade.pnl.startsWith("+") ? "var(--pos)" : "var(--neg)", fontWeight: 600 }}>{trade.pnl}</td>
                <td className="mono muted">{trade.strategy}</td>
                <td><span className={`chip ${trade.status === "STOP" ? "err" : trade.status === "OPEN" ? "brand" : "neut"}`}>{trade.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AutonomousAgentPanel({
  proposals,
  tokenHistory,
  health,
  geminiConfigured,
  busy,
  scannerQuery,
  onScannerQuery,
  onRunScanner,
  onOpenSettings,
  onOpenBot,
}: {
  proposals: CopilotProposal[];
  tokenHistory: TokenDiscoveryHistoryRecord[];
  health: Health;
  geminiConfigured: boolean;
  busy: boolean;
  scannerQuery: string;
  onScannerQuery: (value: string) => void;
  onRunScanner: (mode?: ScannerExecutionMode) => void;
  onOpenSettings: () => void;
  onOpenBot: () => void;
}) {
  const pending = proposals.filter((proposal) => proposal.status === "pending").length;
  const executed = proposals.filter((proposal) => proposal.status === "executed").length;
  const blocked = tokenHistory.filter((record) => record.recommendation === "skip").length;
  const latestSignal = proposals[0] || null;
  const locked = health.orderSubmissionEnabled === false;
  const readiness = scannerReadiness({ geminiConfigured, health });

  return (
    <section className={`card bot-readiness ${readiness.ready ? "ready" : "needs-action"}`} aria-labelledby="autonomous-agent-title">
      <div>
        <span className="eyebrow">Autonomous agent</span>
        <h2 id="autonomous-agent-title">Mode 1 roda paper sozinho. Mode 2 espera sua aprovacao.</h2>
        <p>
          O scanner procura oportunidades, aplica filtros de volume, liquidez e seguranca, e grava propostas.
          No modo paper auto ele tambem executa a simulacao no ledger. Dinheiro real continua bloqueado.
        </p>
      </div>
      <div className="bot-readiness-meta" aria-label="Autonomous agent status">
        <Detail label="Analyzed" value={String(tokenHistory.length)} />
        <Detail label="Blocked" value={String(blocked)} />
        <Detail label="Pending" value={String(pending)} />
        <Detail label="Executed paper" value={String(executed)} />
        <Detail label="Real orders" value={locked ? "blocked" : "armed"} />
      </div>
      <div
        className={`scanner-readiness ${readiness.ready ? "ready" : "blocked"}`}
        role="status"
        aria-live="polite"
        aria-label={readiness.ready ? "Scanner pronto" : "Scanner pausado"}
        id="overview-scanner-readiness-status"
      >
        {readiness.ready ? <Check size={14} /> : <AlertTriangle size={14} />}
        <strong>{readiness.ready ? "Scanner pronto" : "Scanner pausado"}</strong>
        <span>{readiness.status}</span>
      </div>
      <div className="field-grid">
        <label>
          Scanner query
          <input value={scannerQuery} onChange={(event) => onScannerQuery(event.target.value)} placeholder="USDC, SOL, BASE, meme" />
        </label>
        <label>
          Latest signal
          <input value={latestSignal ? `${latestSignal.symbol || shortId(latestSignal.token_address)} - ${proposalScorePct(latestSignal.score)}/100` : "none yet"} readOnly />
        </label>
      </div>
      <div className="plan-actions">
        <button className="btn primary" disabled={busy || !readiness.ready} onClick={() => onRunScanner("paper_auto")} aria-describedby="overview-scanner-readiness-status">
          <Search size={13} />
          Run paper scanner
        </button>
        <button className="btn" disabled={busy || !readiness.ready} onClick={() => onRunScanner("copilot")} aria-describedby="overview-scanner-readiness-status">
          <Bell size={13} />
          Queue approvals
        </button>
        <button className="btn" onClick={geminiConfigured ? onOpenBot : onOpenSettings}>
          <Bot size={13} />
          {geminiConfigured ? "Open bot modes" : "Connect Gemini"}
        </button>
      </div>
    </section>
  );
}

function DashboardEmptyState({ icon, title, body, action }: { icon: ReactNode; title: string; body: string; action?: ReactNode }) {
  return (
    <div className="dashboard-empty-state" role="status">
      <span>{icon}</span>
      <strong>{title}</strong>
      <p>{body}</p>
      {action ? <div>{action}</div> : null}
    </div>
  );
}

function OnboardingGuide({
  geminiConfigured,
  geminiModel,
  firstQuestionReady,
  onOpenSettings,
  onOpenBot,
}: {
  geminiConfigured: boolean;
  geminiModel?: string;
  firstQuestionReady: boolean;
  onOpenSettings: () => void;
  onOpenBot: () => void;
}) {
  const steps = [
    {
      title: "Conta criada",
      body: "Seu workspace autenticado esta pronto.",
      complete: true,
      icon: <Check size={15} />,
    },
    {
      title: "Configurar Gemini API key",
      body: geminiConfigured
        ? `Gemini conectado${geminiModel ? ` com ${geminiModel}` : ""}.`
        : "Adicione a chave em Settings para liberar respostas do bot.",
      complete: geminiConfigured,
      icon: <KeyRound size={15} />,
      action: { label: geminiConfigured ? "Gerenciar chave" : "Configurar chave", onClick: onOpenSettings },
    },
    {
      title: "Rodar scanner autonomo",
      body: geminiConfigured ? "Use Mode 1 para paper auto ou Mode 2 para fila de aprovacao." : "Depois da chave, abra o bot para validar o fluxo.",
      complete: geminiConfigured,
      icon: <Bot size={15} />,
      action: { label: "Abrir bot", onClick: onOpenBot },
    },
    {
      title: "Validar fila de propostas",
      body: firstQuestionReady ? "O chat explica a fila, mas o scanner e quem trabalha em background." : "A explicacao depende da credencial Gemini.",
      complete: firstQuestionReady,
      icon: <Play size={15} />,
      action: { label: "Testar no bot", onClick: onOpenBot },
    },
    {
      title: "Entender modos de risco",
      body: "Mode 3 de dinheiro real fica bloqueado ate flags, aprovacao humana e limites duros estarem ativos.",
      complete: true,
      icon: <Shield size={15} />,
    },
  ];

  return (
    <section className="card onboarding-guide" aria-labelledby="onboarding-title">
      <div className="onboarding-top">
        <div>
          <span className="eyebrow">Primeiros passos</span>
          <h2 id="onboarding-title">Coloque o bot para rodar sem sair do modo seguro</h2>
          <p>Complete o basico pos-login: chave Gemini, bot aberto, primeira pergunta e limites de operacao claros.</p>
        </div>
        <div className={`onboarding-status ${geminiConfigured ? "ok" : "warn"}`}>
          {geminiConfigured ? <Check size={14} /> : <AlertTriangle size={14} />}
          {geminiConfigured ? "Gemini configurado" : "Gemini nao configurado"}
        </div>
      </div>
      <div className="onboarding-steps">
        {steps.map((step) => (
          <div className={`onboarding-step ${step.complete ? "complete" : "pending"}`} key={step.title}>
            <span className="onboarding-icon">{step.complete ? <Check size={15} /> : step.icon}</span>
            <div>
              <strong>{step.title}</strong>
              <p>{step.body}</p>
              {step.action ? (
                <button className={`text-action ${step.complete ? "" : "primary"}`} onClick={step.action.onClick}>
                  {step.action.label}
                  <ArrowRight size={13} />
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ProductionAuditPage({ health, auditEvents }: { health: Health; auditEvents: AuditEvent[] }) {
  const pillars = [
    { id: "saas", name: "SaaS Core", score: 95, done: 4, total: 5, summary: "Users, hashed API keys, revocation, and dashboard endpoints are wired." },
    { id: "paper", name: "Paper Ledger", score: 88, done: 4, total: 5, summary: "Trades persist with fees, slippage, and paper-only enforcement." },
    { id: "scanner", name: "Agent Scanner", score: 78, done: 3, total: 5, summary: "Scanner, risk filters, proposals, and paper execution are wired." },
    { id: "bot", name: "Bot Modes", score: 82, done: 4, total: 5, summary: "Paper auto and approval queue are live; real autonomy stays locked." },
    { id: "security", name: "Security", score: 80, done: 3, total: 6, summary: "Rate limits, headers, and audit logs are active." },
    { id: "obs", name: "Observability", score: 85, done: 3, total: 5, summary: "Health, ready, metrics, request IDs, and Sentry hooks are present." },
    { id: "billing", name: "Billing", score: 70, done: 2, total: 5, summary: "Quota and webhook tables exist; plan metering is next." },
    { id: "infra", name: "Infra", score: 88, done: 3, total: 5, summary: "Docker, VM deploy, and migration gates are configured." },
  ];
  const [active, setActive] = useState(pillars[2].id);
  const activePillar = pillars.find((pillar) => pillar.id === active) || pillars[0];

  return (
    <div className="page">
      <PageHead title="Production Readiness Audit" subtitle={`8 pillars, ${auditEvents.length || 1} recent control events, ${health.mode} mode.`} actions={<button className="btn primary"><Check size={13} /> Re-run audit</button>} />
      <div className="card audit-hero">
        <AuditRing score={82} />
        <div className="col gap-12">
          <div className="row gap-8 wrap">
            <span className="chip brand">B+ production-track</span>
            <span className="chip warn">mainnet settlement gated</span>
            <span className="chip ok">paper trading ready</span>
          </div>
          <p className="audit-verdict">Production-ready for paper trading. Mainnet settlement and autonomous order submission remain blocked until Circle Gateway and runtime hard limits pass audit.</p>
          <div className="audit-stats">
            <Detail label="API" value={health.ok ? "healthy" : "degraded"} />
            <Detail label="Orders" value={health.orderSubmissionEnabled ? "armed" : "blocked"} />
            <Detail label="Mode" value={health.mode} />
            <Detail label="Events" value={String(auditEvents.length || 1)} />
          </div>
        </div>
      </div>
      <Divider label="Pillars - click to inspect" />
      <div className="pillar-grid">
        {pillars.map((pillar) => (
          <button className={`card pillar ${active === pillar.id ? "selected" : ""}`} key={pillar.id} onClick={() => setActive(pillar.id)}>
            <span>{pillar.name}</span>
            <strong>{pillar.score}%</strong>
            <div className={`bar ${pillar.score < 70 ? "err" : pillar.score < 80 ? "warn" : ""}`}><span style={{ width: `${pillar.score}%` }} /></div>
            <small>{pillar.done}/{pillar.total} controls passing</small>
          </button>
        ))}
      </div>
      <Divider label={`${activePillar.name} - ${activePillar.score}%`} />
      <div className="card">
        <CardHead title={activePillar.summary} sub="Required actions are kept out of mainnet execution until resolved." />
        <table className="table">
          <thead><tr><th>Control</th><th>Evidence</th><th>Status</th></tr></thead>
          <tbody>
            {auditRows(activePillar.id).map((row) => (
              <tr key={row.label}>
                <td className="bold">{row.label}</td>
                <td className="mono muted">{row.evidence}</td>
                <td><span className={`chip ${row.status === "PASS" ? "ok" : row.status === "BLOCKER" ? "err" : "warn"}`}>{row.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PaperPage({
  account,
  health,
  dashboard,
  proposals,
  tokenHistory,
  chartData,
  tokenAddress,
  scannerQuery,
  amount,
  busy,
  onTokenAddress,
  onScannerQuery,
  onAmount,
  onSimulate,
  onRunScanner,
}: {
  account: Account;
  health: Health;
  dashboard: Dashboard;
  proposals: CopilotProposal[];
  tokenHistory: TokenDiscoveryHistoryRecord[];
  chartData: NonNullable<Dashboard["last_30_days_chart_points"]>;
  tokenAddress: string;
  scannerQuery: string;
  amount: number;
  busy: boolean;
  onTokenAddress: (value: string) => void;
  onScannerQuery: (value: string) => void;
  onAmount: (value: number) => void;
  onSimulate: () => void;
  onRunScanner: (mode?: ScannerExecutionMode) => void;
}) {
  const executed = proposals.filter((proposal) => proposal.status === "executed");
  const pending = proposals.filter((proposal) => proposal.status === "pending");
  const paperPnl = (dashboard.realized_pnl_usd || 0) + (dashboard.unrealized_pnl_usd || 0);
  const chartVolumes = chartData.map((point) => point.volume_usd);
  const hasChart = chartVolumes.some((value) => value > 0);
  const latestExecuted = executed[0] || null;
  const blocked = tokenHistory.filter((record) => record.recommendation === "skip").length;
  const geminiConfigured = Boolean(account.provider_credentials?.gemini?.configured);
  const hasApiKey = (dashboard.active_api_keys || 0) > 0;
  const scannerState = scannerReadiness({ geminiConfigured, health, hasApiKey });
  const canRunScanner = scannerState.ready;
  const scannerStatus = scannerState.status;
  const scannerBlockedReasons = scannerState.blockers;
  const paperMode2Status = pending.length
    ? `${pending.length} proposta${pending.length === 1 ? "" : "s"} aguardando aprovacao humana`
    : canRunScanner
      ? "pronto para enfileirar aprovacoes paper"
      : "aguardando prerequisitos do scanner";

  return (
    <div className="page">
      <PageHead title="Paper Trading" subtitle="Ledger real do workspace: simulacoes, propostas executadas e custos do modelo paper." actions={<span className="chip ok">PAPER ONLY</span>} />
      <div className="three-grid">
        <div className="card">
          <CardHead title="Realism toggles" sub="What gets subtracted from virtual P&L" />
          {[
            ["Network gas", "$0.0008/tx", true],
            ["DEX swap fee", "0.30%", true],
            ["Slippage model", "buy +5% / sell -5%", true],
            ["Contract transfer tax", "5% buy + 5% sell", true],
            ["MEV loss model", "probabilistic", false],
          ].map(([label, value, active]) => <ToggleRow key={String(label)} label={String(label)} value={String(value)} active={Boolean(active)} />)}
        </div>
        <div className="card">
          <CardHead title="Autonomous paper scanner" sub="POST /api/v1/copilot/scan" badge="Mode 1" />
          <div className={`scanner-readiness ${canRunScanner ? "ready" : "blocked"}`} role="status" aria-live="polite">
            {canRunScanner ? <Check size={14} /> : <AlertTriangle size={14} />}
            <span>{scannerStatus}</span>
          </div>
          <div className="execution-state-grid paper-mode-state" role="list" aria-label="Paper trading execution mode state">
            <div className="execution-state-row" role="listitem">
              <span className={`chip ${canRunScanner ? "ok" : "warn"}`}>Mode 1</span>
              <div>
                <strong>Autonomous paper scanner</strong>
                <p>{canRunScanner ? "Ready to scan, score, and execute paper/testnet trades." : scannerBlockedReasons.join(", ")}</p>
              </div>
            </div>
            <div className="execution-state-row" role="listitem">
              <span className={`chip ${pending.length ? "warn" : canRunScanner ? "ok" : "neut"}`}>Mode 2</span>
              <div>
                <strong>Approval queue</strong>
                <p>{paperMode2Status}. No paper approval executes until a human confirms it.</p>
              </div>
            </div>
            <div className="execution-state-row" role="listitem">
              <span className="chip err">Mode 3</span>
              <div>
                <strong>Real autonomy locked</strong>
                <p>No live funds, autonomous signing, or mainnet order submission from this runtime.</p>
              </div>
            </div>
          </div>
          <div className="field-grid">
            <label>Search universe<input value={scannerQuery} onChange={(event) => onScannerQuery(event.target.value)} /></label>
            <label>Paper size USD<input type="number" min={1} value={amount} onChange={(event) => onAmount(Number(event.target.value))} /></label>
          </div>
          <div className="plan-actions">
            <button className="btn primary" onClick={() => onRunScanner("paper_auto")} disabled={busy || !canRunScanner} aria-describedby="paper-scanner-status"><Search size={13} /> Scan + execute paper</button>
            <button className="btn" onClick={() => onRunScanner("copilot")} disabled={busy || !canRunScanner} aria-describedby="paper-scanner-status"><Bell size={13} /> Queue approvals</button>
          </div>
          <p id="paper-scanner-status" className="muted small">{scannerStatus}</p>
          <Divider label="Manual override" />
          <div className="field-grid">
            <label>Token<input value={tokenAddress} onChange={(event) => onTokenAddress(event.target.value)} /></label>
            <label>Size USD<input type="number" min={1} value={amount} onChange={(event) => onAmount(Number(event.target.value))} /></label>
          </div>
          <button className="btn full" onClick={onSimulate} disabled={busy}><Play size={13} /> Submit single paper override</button>
        </div>
        <div className="card">
          <CardHead title="Ledger outcome" sub="Live paper accounting" />
          <div className="outcome"><span>{money(paperPnl)}</span></div>
          <div className="muted small">realized + unrealized paper P&L</div>
          <DetailList
            rows={[
              ["Paper trades", String(dashboard.total_simulated_trades || 0)],
              ["Executed agent signals", String(executed.length)],
              ["Tokens blocked", String(blocked)],
              ["Pending approvals", String(pending.length)],
              ["Fees modeled", money(dashboard.total_fees_usd || 0)],
            ]}
          />
        </div>
      </div>
      <Divider label="Agent execution history" />
      <div className="card">
        <CardHead
          title={latestExecuted ? `${latestExecuted.symbol || shortId(latestExecuted.token_address)} paper execution` : "No autonomous execution yet"}
          sub={latestExecuted?.rationale || "Run Mode 1 scanner to populate paper executions from real token analysis."}
          badge={latestExecuted ? `${proposalScorePct(latestExecuted.score)}/100` : "waiting"}
        />
        {hasChart ? (
          <EquityChart points={chartVolumes} />
        ) : (
          <DashboardEmptyState
            icon={<LineChart size={18} />}
            title="Paper equity is empty"
            body="The chart fills from authenticated simulations and scanner-approved paper executions, not placeholder backtests."
            action={<button className="btn sm primary" onClick={onSimulate} disabled={busy}>Run manual paper order</button>}
          />
        )}
      </div>
    </div>
  );
}

function BotModesPage({
  account,
  health,
  activeApiKeys,
  mode,
  setMode,
  proposals,
  tokenHistory,
  scannerRunEvidence,
  chatMessages,
  chatInput,
  walletAddress,
  walletChainId,
  walletStatus,
  busy,
  scannerQuery,
  autopilotEnabled,
  autopilotStatus,
  autopilotLastRunAt,
  autopilotRuns,
  autopilotError,
  onScannerQuery,
  onAutopilotEnabled,
  onChatInput,
  onSendAgentMessage,
  onRunScanner,
  onConnectWallet,
  onApprove,
  onOpenSettings,
}: {
  account: Account;
  health: Health;
  activeApiKeys: number;
  mode: BotMode;
  setMode: (mode: BotMode) => void;
  proposals: CopilotProposal[];
  tokenHistory: TokenDiscoveryHistoryRecord[];
  scannerRunEvidence: ScannerRunEvidence | null;
  chatMessages: AgentChatTurn[];
  chatInput: string;
  walletAddress: string;
  walletChainId: string;
  walletStatus: WalletStatus;
  busy: boolean;
  scannerQuery: string;
  autopilotEnabled: boolean;
  autopilotStatus: "idle" | "running" | "paused" | "error";
  autopilotLastRunAt: string | null;
  autopilotRuns: number;
  autopilotError: string | null;
  onScannerQuery: (value: string) => void;
  onAutopilotEnabled: (value: boolean) => void;
  onChatInput: (value: string) => void;
  onSendAgentMessage: (messageOverride?: string) => Promise<void>;
  onRunScanner: (mode?: ScannerExecutionMode) => void;
  onConnectWallet: () => Promise<void>;
  onApprove: (id: number) => void;
  onOpenSettings: () => void;
}) {
  const modeData = {
    sim: { label: "Modo 1 - Paper/Testnet automatico", tag: "AUTO PAPER", desc: "Analisa mercado, filtra risco e executa somente entradas simuladas no ledger.", color: "var(--brand)" },
    copilot: { label: "Modo 2 - Human-in-the-loop", tag: "APROVACAO", desc: "O agente encontra o trade e enfileira a decisao para aprovacao humana.", color: "var(--warn)" },
    auto: { label: "Modo 3 - Autonomo real bloqueado", tag: "LOCKED", desc: "Dinheiro real e assinatura autonoma continuam bloqueados ate hard limits, flags e auditoria passarem.", color: "var(--err)" },
  } satisfies Record<BotMode, { label: string; tag: string; desc: string; color: string }>;
  const current = modeData[mode];
  const pendingProposals = proposals.filter((proposal) => proposal.status === "pending").length;
  const executedProposals = proposals.filter((proposal) => proposal.status === "executed").length;
  const blockedDiscoveries = tokenHistory.filter((record) => record.recommendation === "skip").length;
  const analyzedTokens = tokenHistory.length || proposals.length;
  const geminiCredential = account.provider_credentials?.gemini;
  const geminiConfigured = Boolean(geminiCredential?.configured);
  const hasApiKey = activeApiKeys > 0;
  const realModeEnabled = Boolean(account.real_mode_enabled);
  const realModeDailyLimit = account.real_mode_daily_limit_usd ?? 50;
  const paperScannerState = scannerReadiness({ geminiConfigured, health, hasApiKey });
  const scannerState = scannerReadiness({
    geminiConfigured,
    health,
    hasApiKey,
    blockers: [
      mode === "copilot" && walletStatus !== "connected" ? "MetaMask nao conectada" : null,
      mode === "copilot" && !realModeEnabled ? "real mode precisa de liberacao do org" : null,
      mode === "auto" ? "modo real autonomo bloqueado" : null,
    ].filter((reason): reason is string => Boolean(reason)),
  });
  const botReady = paperScannerState.ready;
  const scannerBlockedReasons = scannerState.blockers;
  const readinessLabel = botReady ? "Ciclo autonomo pronto" : "Ciclo autonomo pausado";
  const readinessBody = botReady && mode === "copilot" && realModeEnabled && walletStatus === "connected"
    ? "Mode 2 fica pronto para criar propostas reais com aprovacao humana e assinatura MetaMask, sem envio autonomo de ordem."
    : botReady
    ? "Mode 1 fica armado para rodar scanner periodico, simular entradas e registrar o resultado sem voce conversar com o bot."
    : scannerBlockedReasons.length
      ? `Falta resolver: ${scannerBlockedReasons.join(", ")}.`
      : "O ciclo esta pausado pelo operador.";
  const walletStatusText = {
    idle: "not connected",
    connected: shortId(walletAddress),
    missing: "wallet missing",
    error: "connection failed",
  } satisfies Record<WalletStatus, string>;
  const walletTone = walletStatus === "connected" ? "ok" : walletStatus === "idle" ? "neut" : "warn";
  const settlementMode = health.settlement_auth_mode || "manual";
  const scannerEvidenceMode =
    scannerRunEvidence?.executionMode === "real_approval"
      ? "Mode 2 real approval"
      : scannerRunEvidence?.executionMode === "copilot"
        ? "Mode 2 paper approval queue"
        : "Mode 1 paper auto";
  const scannerEvidenceCreated = scannerRunEvidence?.created.slice(0, 3) || [];
  const scannerEvidenceSkipped = scannerRunEvidence?.skipped.slice(0, 3) || [];
  const latestProposal = proposals[0] || null;
  const latestDiscovery = tokenHistory[0] || null;
  const canRunScanner = mode !== "auto" && scannerState.ready;
  const paperReadinessValue = botReady
    ? "ready for autonomous paper/testnet execution"
    : scannerBlockedReasons.length
      ? scannerBlockedReasons.join(", ")
      : "paused by operator";
  const approvalStateValue = pendingProposals > 0
    ? `${pendingProposals} pending human approval${pendingProposals === 1 ? "" : "s"}`
    : mode === "copilot" && canRunScanner
      ? "ready to queue approval proposals"
      : "no approvals queued";
  const approvalStateTone = pendingProposals > 0 ? "warn" : mode === "copilot" && canRunScanner ? "ok" : "neut";
  const scannerNextAction = scannerRunEvidence
    ? scannerRunEvidence.executionMode === "paper_auto"
      ? scannerRunEvidence.paperExecutedCount > 0 || scannerRunEvidence.created.length > 0
        ? "Review paper ledger and keep Mode 1 running; no human approval or real order is required."
        : "Tune the search universe or provider credentials, then rerun Mode 1 before queuing approvals."
      : scannerRunEvidence.executionMode === "real_approval"
        ? "Review the manual approval queue and sign only from MetaMask; autonomous order submission remains blocked."
        : "Review pending proposals before execution; keep rejected or cancelled items as no-trade evidence."
    : "Run Mode 1 paper scanner first, or switch to Mode 2 only after org and wallet gates are ready.";
  const autonomyState =
    mode === "sim" && autopilotEnabled && botReady
      ? autopilotStatus === "running"
        ? "rodando agora"
        : "armado"
      : mode === "auto"
        ? "bloqueado"
        : mode === "copilot" && realModeEnabled && walletStatus === "connected"
          ? "aguardando aprovacao humana"
          : "pausado";

  return (
    <div className="page">
      <PageHead
        title="Agente de Trading IA"
        subtitle={`O produto nao e chat: o ciclo principal e ${current.label}. Chat fica como diagnostico secundario.`}
        actions={
          <div className="mode-tabs">
            {(["sim", "copilot", "auto"] as BotMode[]).map((item) => (
              <button
                className={`${mode === item ? `active ${item === "auto" ? "skynet" : item}` : ""}`}
                key={item}
                onClick={() => setMode(item)}
                aria-pressed={mode === item}
              >
                {modeData[item].label}
              </button>
            ))}
          </div>
        }
      />
      <div className="bot-safety-strip" aria-label="Bot mode safety summary">
        <div>
          <span>Modo ativo</span>
          <strong>{current.label}</strong>
        </div>
        <div>
          <span>Ciclo</span>
          <strong>{autonomyState}</strong>
        </div>
        <div>
          <span>Gemini</span>
          <strong>{geminiConfigured ? "configured" : "missing"}</strong>
        </div>
        <div>
          <span>API keys</span>
          <strong>{activeApiKeys}</strong>
        </div>
        <div>
          <span>Aprovacoes</span>
          <strong>{pendingProposals}</strong>
        </div>
        <div>
          <span>Gate Mode 2</span>
          <strong>{realModeEnabled ? `manual ate ${money(realModeDailyLimit)}/dia` : "org locked"}</strong>
        </div>
        <div>
          <span>Dinheiro real</span>
          <strong>{realModeEnabled ? "manual only" : "locked / paper-only"}</strong>
        </div>
      </div>
      <section className={`card bot-readiness ${botReady ? "ready" : "needs-action"}`} aria-labelledby="bot-readiness-title" aria-live="polite">
        <div>
          <span className="eyebrow">Motor autonomo</span>
          <h2 id="bot-readiness-title">{readinessLabel}</h2>
          <p>{readinessBody}</p>
        </div>
        <div className="bot-readiness-meta" aria-label="Bot readiness checks">
          <Detail label="Gemini" value={geminiConfigured ? geminiCredential?.key_hint || "configured" : "missing"} />
          <Detail label="API keys" value={String(activeApiKeys)} />
          <Detail label="Model" value={geminiCredential?.model || "gemini-3.1-pro-preview"} />
          <Detail label="API" value={health.ok ? "online" : "unstable"} />
          <Detail label="Ultimo ciclo" value={compactDate(autopilotLastRunAt)} />
        </div>
        <button className={geminiConfigured && hasApiKey ? "btn" : "btn primary"} onClick={onOpenSettings}>
          <KeyRound size={13} />
          {geminiConfigured && hasApiKey ? "Gerenciar credenciais" : "Conectar credenciais"}
        </button>
      </section>
      <section
        className={`scanner-readiness ${scannerState.ready ? "ready" : "blocked"}`}
        role="status"
        aria-live="polite"
        aria-label={scannerState.ready ? "Prontidao do scanner" : "Bloqueios do scanner"}
        id="bot-scanner-readiness-status"
      >
        {scannerState.ready ? <Check size={14} /> : <AlertTriangle size={14} />}
        <strong>{scannerState.ready ? "Scanner pronto" : "Scanner pausado"}</strong>
        <span>{scannerState.status}</span>
      </section>
      <section className="card execution-state-panel" aria-label="Paper testnet readiness and approval state" aria-live="polite">
        <CardHead
          title="Paper/testnet readiness and approval state"
          sub="Shows what can run now, what needs a human, and what remains locked."
          badge={botReady ? "paper/testnet ready" : "setup required"}
        />
        <div className="execution-state-grid" role="list" aria-label="Execution mode state details">
          <div className="execution-state-row" role="listitem">
            <span className={`chip ${botReady ? "ok" : "warn"}`}>Mode 1</span>
            <div>
              <strong>Paper/testnet autonomous scanner</strong>
              <p>{paperReadinessValue}</p>
            </div>
          </div>
          <div className="execution-state-row" role="listitem">
            <span className={`chip ${approvalStateTone}`}>Mode 2</span>
            <div>
              <strong>Approval queue</strong>
              <p>{approvalStateValue}. Human approval is required before queued proposals move forward.</p>
            </div>
          </div>
          <div className="execution-state-row" role="listitem">
            <span className="chip err">Mode 3</span>
            <div>
              <strong>Real autonomous execution</strong>
              <p>Locked: no live funds, autonomous signing, or mainnet order submission from this runtime.</p>
            </div>
          </div>
        </div>
      </section>
      <section className="card scanner-evidence-card" aria-label="Scanner run evidence" aria-live="polite">
        <CardHead
          title="Scanner run evidence"
          sub={scannerRunEvidence ? `Last run: ${scannerEvidenceMode} for ${scannerRunEvidence.query}` : "Run Mode 1 or Mode 2 to capture account-scoped evidence"}
          badge={scannerRunEvidence ? compactDate(scannerRunEvidence.ranAt) : "no run yet"}
        />
        {scannerRunEvidence ? (
          <>
            <div className="scanner-evidence-grid" role="list" aria-label="Latest scanner evidence summary">
              <Detail label="Run ID" value={String(scannerRunEvidence.id)} />
              <Detail label="Run mode" value={scannerEvidenceMode} />
              <Detail label="Status" value={scannerRunEvidence.status} />
              <Detail label="Query" value={scannerRunEvidence.query} />
              <Detail label="Candidates" value={String(scannerRunEvidence.candidateCount)} />
              <Detail label="Created" value={String(scannerRunEvidence.created.length)} />
              <Detail label="Queued" value={String(scannerRunEvidence.queuedCount)} />
              <Detail label="Paper executed" value={String(scannerRunEvidence.paperExecutedCount)} />
              <Detail label="Blocked" value={String(scannerRunEvidence.blockedCount)} />
            </div>
            <div className="scanner-next-action" aria-label="Scanner next action">
              <span>Next operator action</span>
              <strong>{scannerNextAction}</strong>
            </div>
            <div className="scanner-evidence-list">
              <div>
                <span className="eyebrow">Created proposals</span>
                {scannerEvidenceCreated.length ? (
                  scannerEvidenceCreated.map((proposal) => (
                    <div className="scanner-evidence-row" key={`created-${scannerRunEvidence.id}-${proposal.id}`}>
                      <span>{proposal.symbol || proposal.token_address}</span>
                      <strong>{proposal.status === "executed" ? "paper executed" : proposal.status}</strong>
                      <em>{proposalScorePct(proposal.score)}/100</em>
                    </div>
                  ))
                ) : (
                  <p className="empty-list">No proposals passed this scan.</p>
                )}
              </div>
              <div>
                <span className="eyebrow">Audit blocks</span>
                {scannerEvidenceSkipped.length ? (
                  scannerEvidenceSkipped.map((item) => (
                    <div className="scanner-evidence-row" key={`skipped-${scannerRunEvidence.id}-${item.token_address}`}>
                      <span>{item.token_address}</span>
                      <strong>{item.reason.replaceAll("_", " ")}</strong>
                      <em>{proposalScorePct(item.score)}/100</em>
                    </div>
                  ))
                ) : (
                  <p className="empty-list">No audit blocks in the latest run.</p>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="dashboard-empty-state compact">
            <span><Search size={16} /></span>
            <strong>No scanner run captured in this browser session</strong>
            <p>{scannerNextAction}</p>
          </div>
        )}
      </section>
      <div className="autonomy-grid">
        <div className="card autonomy-primary">
          <CardHead
            title="Ciclo Mode 1 - paper auto"
            sub="Analise estatistica, filtro de risco, simulacao e auditoria sem ordem real"
            badge={autopilotStatus === "running" ? "running" : autopilotEnabled ? "armed" : "paused"}
          />
          <div className="agent-cycle-status" aria-label="Autonomous paper cycle status">
            <div>
              <span>Status</span>
              <strong>{autonomyState}</strong>
            </div>
            <div>
              <span>Query</span>
              <strong>{scannerQuery || "USDC"}</strong>
            </div>
            <div>
              <span>Ciclos</span>
              <strong>{autopilotRuns}</strong>
            </div>
            <div>
              <span>Ultimo</span>
              <strong>{compactDate(autopilotLastRunAt)}</strong>
            </div>
          </div>
          <div className="field-grid">
            <label>
              Universo de busca
              <input value={scannerQuery} onChange={(event) => onScannerQuery(event.target.value)} placeholder="USDC, SOL, BASE, meme" />
            </label>
            <label>
              Ultimo sinal
              <input
                value={
                  latestProposal
                    ? `${latestProposal.symbol || shortId(latestProposal.token_address)} - ${proposalScorePct(latestProposal.score)}/100`
                    : latestDiscovery
                      ? `${latestDiscovery.symbol || shortId(latestDiscovery.token_address)} - ${proposalScorePct(latestDiscovery.score || 0)}/100`
                      : "aguardando primeiro ciclo"
                }
                readOnly
              />
            </label>
          </div>
          <div className="plan-actions">
            <button className={autopilotEnabled ? "btn" : "btn primary"} onClick={() => onAutopilotEnabled(!autopilotEnabled)} disabled={mode === "auto"}>
              {autopilotEnabled ? <Pause size={13} /> : <Play size={13} />}
              {autopilotEnabled ? "Pausar ciclo" : "Ativar ciclo automatico"}
            </button>
            <button className="btn primary" disabled={busy || !botReady || mode !== "sim"} onClick={() => onRunScanner("paper_auto")} aria-describedby="bot-scanner-readiness-status">
              <Search size={13} />
              Rodar paper agora
            </button>
            <button className="btn" disabled={busy || !canRunScanner || mode !== "copilot"} onClick={() => onRunScanner("real_approval")} aria-describedby="bot-scanner-readiness-status">
              <Wallet size={13} />
              Enfileirar aprovacao real
            </button>
          </div>
          {autopilotError ? <p className="field-hint danger" role="alert">Ultimo ciclo falhou: {autopilotError}</p> : null}
          {scannerBlockedReasons.length > 0 ? (
            <div className="cycle-blockers" aria-label="Autonomous cycle blockers">
              {scannerBlockedReasons.map((reason) => (
                <span className="chip warn" key={reason}>{reason}</span>
              ))}
            </div>
          ) : null}
        </div>
        <div className="card decision-card">
          <CardHead title="Pipeline de decisao" sub={mode === "copilot" ? "O agente pesquisa; voce assina a aprovacao manual antes de qualquer acao real." : "O que o agente faz sozinho antes de registrar P&L paper"} badge={current.tag} />
          <div className="decision-steps">
            {[
              ["1", "Descobrir", "DexScreener e provedores somente leitura"],
              ["2", "Auditar", "honeypot, liquidez, tax, momentum e volume"],
              ["3", mode === "copilot" ? "Assinar aprovacao" : "Executar paper", mode === "copilot" ? "MetaMask registra autorizacao humana; backend nao envia ordem sozinho" : "simula entrada sem carteira real"],
              ["4", "Aprender", "ledger, P&L, score e auditoria por workspace"],
            ].map(([step, title, body]) => (
              <div className="decision-step" key={step}>
                <span>{step}</span>
                <div>
                  <strong>{title}</strong>
                  <p>{body}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="dashboard-grid">
        <div className="card mode-card" style={{ borderColor: current.color }}>
          <span className="chip brand">{current.tag}</span>
          <h2>{current.label} mode</h2>
          <p>{current.desc}</p>
          <ul className="check-list">
            <li><Check size={14} /> Private keys never leave MetaMask.</li>
            <li><Check size={14} /> Mainnet orders are blocked by backend policy.</li>
            <li><Check size={14} /> Every decision is written to the audit log.</li>
          </ul>
        </div>
        <div className="card">
          <CardHead title="Risk funnel" sub="Workspace scanner results" />
          {[
            ["Tokens analyzed", analyzedTokens, "var(--ink-2)"],
            ["Blocked by audit", blockedDiscoveries, "var(--err)"],
            ["Queued for approval", pendingProposals, "var(--warn)"],
            ["Paper executed", executedProposals, "var(--brand)"],
            ["Real orders sent", 0, "var(--plasma)"],
          ].map(([label, value, color]) => (
            <div className="funnel-row" key={String(label)}>
              <span>{label}</span>
              <div className="bar"><span style={{ width: `${Math.max(4, (Number(value) / Math.max(1, analyzedTokens)) * 100)}%`, background: String(color) }} /></div>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      </div>
      <Divider label="Diagnostico secundario - nao e o fluxo principal" />
      <div className="bot-workspace compact">
        <div className="card agent-chat-card">
          <CardHead
            title="Agent console"
            sub={geminiConfigured ? `Diagnostico usando ${geminiCredential?.model || "modelo da conta"}` : "Configure Gemini para perguntas manuais"}
            badge={geminiConfigured ? "diagnostic" : "missing key"}
          />
          <div className="agent-chat-log compact" aria-live="polite">
            {chatMessages.map((turn, index) => (
              <div className={`agent-turn ${turn.role}`} key={`${turn.role}-${index}-${turn.text.slice(0, 12)}`}>
                <span>{turn.role === "user" ? "Voce" : "TTM Bot"}</span>
                <p>{turn.text}</p>
              </div>
            ))}
          </div>
          <div className="agent-prompts">
            {["Explique o ultimo ciclo", "Por que um token foi bloqueado?", "O que falta para Mode 3?"].map((prompt) => (
              <button className="btn sm" key={prompt} onClick={() => onSendAgentMessage(prompt)} disabled={busy || !geminiConfigured}>
                {prompt}
              </button>
            ))}
          </div>
          <form
            className="agent-chat-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void onSendAgentMessage();
            }}
          >
            <input
              value={chatInput}
              onChange={(event) => onChatInput(event.target.value)}
              placeholder="Diagnostico do ciclo, fila ou risco..."
              disabled={busy || !geminiConfigured}
            />
            <button className="btn primary icon-only" type="submit" disabled={busy || !geminiConfigured || !chatInput.trim()} aria-label="Enviar mensagem">
              <Send size={15} />
            </button>
          </form>
        </div>
        <div className="card wallet-card compact">
          <CardHead title="Gate manual de dinheiro real" sub="Somente para Mode 2/3 futuro; Mode 1 nao usa carteira" badge={settlementMode} />
          <div className="wallet-connect-panel compact">
            <div className="wallet-icon"><Wallet size={18} /></div>
            <div>
              <span className={`chip ${walletTone}`}>{walletStatusText[walletStatus]}</span>
              <h3>{walletStatus === "connected" ? "Carteira conectada" : "Carteira fora do Mode 1"}</h3>
              <p>
                {settlementMode === "programmatic"
                  ? "Settlement programatico exige flags e auditoria; assinatura do navegador continua explicita."
                  : "Nenhuma chave privada vai ao backend. Mode 1 opera sem wallet e sem dinheiro real."}
              </p>
            </div>
          </div>
          <DetailList
            rows={[
              ["Provider", health.wallet_provider || "browser injected"],
              ["Wallet set", health.wallet_set_id || "not linked"],
              ["Wallets loaded", health.wallets_loaded ? "yes" : "no"],
              ["Chain", walletChainId || "not selected"],
              ["Orders", health.orderSubmissionEnabled ? "armed" : "blocked"],
            ]}
          />
          <button className="btn full" onClick={onConnectWallet} disabled={busy}>
            <Wallet size={13} />
            Conectar wallet
          </button>
        </div>
      </div>
      <Divider label="Pending approvals - copilot queue" />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Token</th><th>Chain</th><th>Score</th><th>Suggested size</th><th>Status</th><th /></tr></thead>
          <tbody>
            {proposals.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">No pending approvals for this workspace yet. Run the scanner or a paper simulation to create account-scoped copilot proposals before approving execution.</td>
              </tr>
            ) : proposals.map((proposal) => (
              <tr key={proposal.id}>
                <td className="bold">{proposal.symbol || proposal.token_address}</td>
                <td>{proposal.chain}</td>
                <td><span className="chip ok">{proposalScorePct(proposal.score)}/100</span></td>
                <td className="num">{money(proposal.amount_usd)}</td>
                <td>
                  <div className="proposal-status-cell">
                    <span className={`chip ${proposalStatusTone(proposal.status)}`}>{proposalStatusLabel(proposal.status)}</span>
                    {proposalStatusFeedback(proposal) ? <span className="muted small">{proposalStatusFeedback(proposal)}</span> : null}
                  </div>
                </td>
                <td>
                  {isPendingProposalStatus(proposal.status) ? (
                    <button
                      className="btn sm primary"
                      disabled={busy || (proposal.mode === "real" && walletStatus !== "connected")}
                      onClick={() => onApprove(proposal.id)}
                    >
                      {proposal.mode === "real" ? "Sign manual approval" : "Approve proposal"}
                    </button>
                  ) : (
                    <span className="muted small">{proposalActionLabel(proposal)}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const metadataNumber = (metadata: Record<string, unknown>, key: string) => {
  const value = metadata[key];
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
};

function MarketPage({
  health,
  proposals,
  tokenHistory,
  busy,
  scannerQuery,
  onScannerQuery,
  onRunScanner,
}: {
  health: Health;
  proposals: CopilotProposal[];
  tokenHistory: TokenDiscoveryHistoryRecord[];
  busy: boolean;
  scannerQuery: string;
  onScannerQuery: (value: string) => void;
  onRunScanner: (mode?: ScannerExecutionMode) => void;
}) {
  const providers = Object.entries(health.providers || {});
  const healthyProviders = providers.filter(([, provider]) => provider.status === "ok").length;
  const blockedProviders = providers.filter(([, provider]) => provider.status !== "ok").length;
  const latencySamples = providers
    .map(([, provider]) => provider.latency_ms)
    .filter((latency): latency is number => typeof latency === "number");
  const averageLatencyMs = latencySamples.length
    ? Math.round(latencySamples.reduce((total, latency) => total + latency, 0) / latencySamples.length)
    : null;
  const averageLatency = averageLatencyMs === null ? "not reported" : `${averageLatencyMs}ms`;
  const providerReadiness = providers.length === 0
    ? "No providers reported"
    : blockedProviders > 0
      ? `${blockedProviders} need attention`
      : "All providers healthy";
  const pending = proposals.filter((proposal) => proposal.status === "pending").length;
  const executed = proposals.filter((proposal) => proposal.status === "executed").length;
  const blocked = tokenHistory.filter((record) => record.recommendation === "skip").length;
  const passRate = tokenHistory.length ? Math.round(((tokenHistory.length - blocked) / tokenHistory.length) * 100) : 0;
  const scoreSeries = tokenHistory.slice(0, 12).map((record) => proposalScorePct(record.score || 0)).reverse();

  return (
    <SimplePage title="Market Data" subtitle="Scanner operacional: dados externos somente leitura, auditoria de token e propostas paper por workspace.">
      <div className="card bot-readiness ready">
        <div>
          <span className="eyebrow">Market scanner</span>
          <h2>Analise tokens antes do paper trade.</h2>
          <p>O backend consulta provedores externos, normaliza volume/momentum/liquidez, roda auditoria de contrato e bloqueia dinheiro real.</p>
        </div>
        <div className="field-grid">
          <label>Search query<input value={scannerQuery} onChange={(event) => onScannerQuery(event.target.value)} /></label>
          <label>Execution<input value="paper_auto or approval queue" readOnly /></label>
        </div>
        <div className="plan-actions">
          <button className="btn primary" onClick={() => onRunScanner("paper_auto")} disabled={busy}><Search size={13} /> Scan + paper execute</button>
          <button className="btn" onClick={() => onRunScanner("copilot")} disabled={busy}><Bell size={13} /> Scan to approval queue</button>
        </div>
      </div>
      <div className="kpi-grid">
        <KPI label="Tokens analyzed" value={compactNumber(tokenHistory.length)} delta={`${blocked} blocked by audit`} spark={sparkSeries(scoreSeries, tokenHistory.length)} />
        <KPI label="Pass rate" value={tokenHistory.length ? String(passRate) : "N/A"} unit={tokenHistory.length ? "%" : undefined} delta={`${pending} queued, ${executed} executed`} spark={sparkSeries([blocked, pending, executed, passRate])} sparkColor="var(--plasma)" />
        <KPI label="Connector latency" value={averageLatencyMs === null ? "N/A" : String(averageLatencyMs)} unit={averageLatencyMs === null ? undefined : "ms"} delta={providerReadiness} spark={[55, 52, 48, 46, 44, 42, 41, 42, 40, 42, 42, 42]} sparkColor="var(--signal)" />
        <KPI label="Order submission" value={health.orderSubmissionEnabled ? "ON" : "OFF"} delta="backend policy" spark={[1, 0, 0, 1, 2, 1, 0, 2, 1, 1, 2, 2]} sparkColor="var(--info)" />
      </div>
      <Divider label="Latest token analysis" />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Token</th><th>Chain</th><th>Score</th><th>Volume 24h</th><th>Momentum h1</th><th>Liquidity</th><th>Decision</th></tr></thead>
          <tbody>
            {tokenHistory.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-cell">No token scans yet. Run the market scanner to replace placeholder market cards with audited token candidates.</td>
              </tr>
            ) : tokenHistory.slice(0, 10).map((record) => (
              <tr key={record.id}>
                <td className="bold">{record.symbol || shortId(record.token_address)}</td>
                <td>{record.chain}</td>
                <td><span className={`chip ${record.recommendation === "skip" ? "err" : "ok"}`}>{proposalScorePct(record.score || 0)}/100</span></td>
                <td className="num">{money(metadataNumber(record.metadata, "volume_24h"))}</td>
                <td className="num">{metadataNumber(record.metadata, "momentum_h1").toFixed(2)}%</td>
                <td className="num">{money(metadataNumber(record.metadata, "liquidity_usd"))}</td>
                <td><span className={`chip ${record.recommendation === "skip" ? "err" : record.recommendation === "copilot" ? "warn" : "brand"}`}>{record.recommendation || "review"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Divider label="Provider health" />
      <div className="provider-summary" aria-label="Provider health summary">
        <Detail label="Reported" value={String(providers.length)} />
        <Detail label="Healthy" value={String(healthyProviders)} />
        <Detail label="Latency avg" value={averageLatency} />
        <Detail label="Readiness" value={providerReadiness} />
      </div>
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Provider</th><th>Read-only</th><th>Order submission</th><th>Latency</th><th>Status</th><th>Last error</th></tr></thead>
          <tbody>
            {providers.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">No external provider health reported yet. Market data remains read-only and order submission stays blocked.</td>
              </tr>
            ) : providers.map(([name, provider]) => (
              <tr key={name}>
                <td className="bold">{name}</td>
                <td><span className="chip ok">YES</span></td>
                <td><span className="chip err">BLOCKED</span></td>
                <td className="mono">{provider.latency_ms === null ? "-" : `${provider.latency_ms}ms`}</td>
                <td><span className={`chip ${provider.status === "ok" ? "ok" : provider.status === "degraded" ? "warn" : "neut"}`}>{provider.status}</span></td>
                <td className="muted small">{provider.last_error || "none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SimplePage>
  );
}

function NanopaymentsPage({
  artifacts,
  selectedArtifact,
  selectedArtifactId,
  activePayment,
  nexusJob,
  activeArcProof,
  busy,
  onSelectedArtifact,
  onCreatePayment,
  onVerifyPayment,
  onUnlockDelivery,
}: {
  artifacts: BillableArtifact[];
  selectedArtifact: BillableArtifact | null;
  selectedArtifactId: string;
  activePayment: PaymentIntentRecord | null;
  nexusJob: NexusJob | null;
  activeArcProof: ArcJobProof | null;
  busy: boolean;
  onSelectedArtifact: (value: string) => void;
  onCreatePayment: () => void;
  onVerifyPayment: () => void;
  onUnlockDelivery: () => void;
}) {
  const deliveryUnlocked = Boolean(activePayment?.download_url || nexusJob?.state === "DELIVERED");
  const nextLifecycleAction = deliveryUnlocked
    ? "Delivery is unlocked. Download URL and Arc proof metadata are ready for handoff."
    : activePayment?.status === "verified"
      ? "Payment is verified. Unlock delivery to complete the paid work unit."
      : "Payment requirement is open. Verify the test transaction before delivery unlocks.";
  return (
    <div className="page">
      <PageHead title="Legacy Lab - x402" subtitle="Secondary pay-per-call settlement surface kept for compatibility tests, not the primary trading SaaS workflow." actions={<span className="chip warn">manual settlement gate</span>} />
      <div className="kpi-grid">
        <KPI label="Spent 30d" value="2.847" unit=" USDC" delta="-12% vs prev" spark={[12, 10, 14, 11, 9, 8, 10, 7, 8, 6, 7, 6]} />
        <KPI label="Earned 30d" value="18.42" unit=" USDC" delta="+88% vs prev" spark={[2, 3, 2.5, 4, 5, 4.8, 6, 7, 8, 9, 10, 12]} sparkColor="var(--plasma)" />
        <KPI label="402 to settled" value="98.7" unit="%" delta="0 failed" spark={[95, 96, 97, 96, 98, 98, 99, 98, 99, 99, 98.7, 98.7]} sparkColor="var(--signal)" />
        <KPI label="Avg latency" value="142" unit="ms" delta="p95 settlement" spark={[180, 170, 160, 155, 150, 148, 145, 143, 144, 142, 141, 142]} sparkColor="var(--info)" />
      </div>
      <LifecyclePanel
        artifacts={artifacts}
        selectedArtifact={selectedArtifact}
        selectedArtifactId={selectedArtifactId}
        activePayment={activePayment}
        nexusJob={nexusJob}
        activeArcProof={activeArcProof}
        nextLifecycleAction={nextLifecycleAction}
        busy={busy}
        onSelectedArtifact={onSelectedArtifact}
        onCreatePayment={onCreatePayment}
        onVerifyPayment={onVerifyPayment}
        onUnlockDelivery={onUnlockDelivery}
      />
      <div className="card">
        <CardHead title="x402 flow" sub="Three-phase handshake" badge={deliveryUnlocked ? "DELIVERED" : "LOCKED"} />
        <pre className="code">{`HTTP/1.1 402 Payment Required
WWW-Authenticate: x402

{
  "artifact": "${selectedArtifact?.id || "artifact_delivery_packet"}",
  "price_usdc": "${selectedArtifact?.price_usd ?? 0.001}",
  "network": "arc-testnet",
  "payment_id": "${activePayment?.payment_id || "pending"}",
  "settlement": "${activePayment?.settlement_status || "PENDING"}"
}`}</pre>
      </div>
    </div>
  );
}

function ApiKeysPage({
  keys,
  apiKey,
  keyName,
  busy,
  onApiKey,
  onKeyName,
  onCreate,
  onCopy,
  onRevoke,
}: {
  keys: ApiKeyRecord[];
  apiKey: string;
  keyName: string;
  busy: boolean;
  onApiKey: (value: string) => void;
  onKeyName: (value: string) => void;
  onCreate: () => void;
  onCopy: () => void;
  onRevoke: (id: number) => void;
}) {
  const activeKeys = keys.filter((key) => !key.revoked_at);
  const revokedKeys = keys.length - activeKeys.length;
  const lastUsedAt = activeKeys
    .map((key) => key.last_used_at)
    .filter((value): value is string => Boolean(value))
    .sort((left, right) => new Date(right).getTime() - new Date(left).getTime())[0];

  return (
    <div className="page">
      <PageHead title="API Keys" subtitle="Keys are stored as hashes. Plaintext is shown only once at creation." actions={<button className="btn primary" onClick={onCreate} disabled={busy}><Plus size={13} /> Create key</button>} />
      <div className="card key-create">
        <label>Name<input value={keyName} onChange={(event) => onKeyName(event.target.value)} /></label>
        <label>Active key<input value={apiKey} onChange={(event) => onApiKey(event.target.value)} placeholder="ttm_sk_live_..." /></label>
        <button className="btn" onClick={onCopy} disabled={!apiKey}><Copy size={13} /> Copy</button>
      </div>
      <div className="api-key-summary" aria-label="API key summary">
        <Detail label="Total keys" value={String(keys.length)} />
        <Detail label="Active" value={String(activeKeys.length)} />
        <Detail label="Revoked" value={String(revokedKeys)} />
        <Detail label="Last used" value={compactDate(lastUsedAt)} />
      </div>
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Created</th><th>Last used</th><th>Status</th><th /></tr></thead>
          <tbody>
            {keys.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-cell">No API keys yet. Create a scoped key before running paper simulations from this browser.</td>
              </tr>
            ) : keys.map((key) => (
              <tr key={key.id}>
                <td className="bold">{key.name}</td>
                <td className="mono muted">{key.prefix}...</td>
                <td className="mono muted">{key.scopes}</td>
                <td className="mono">{compactDate(key.created_at)}</td>
                <td className="muted">{compactDate(key.last_used_at)}</td>
                <td><span className={`chip ${key.revoked_at ? "err" : "ok"}`}>{key.revoked_at ? "REVOKED" : "ACTIVE"}</span></td>
                <td>{!key.revoked_at ? <button className="btn sm danger" onClick={() => onRevoke(key.id)}>Revoke key</button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Divider label="Quickstart" />
      <div className="two-grid">
        <CodeCard title="cURL" code={`curl -X POST https://api.ttm.so/api/v1/simulate/order \\
  -H "X-API-Key: $TTM_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"token_address":"0xMOON","side":"BUY","amount":100}'`} />
        <CodeCard title="TypeScript" code={`const result = await fetch("/api/v1/simulate/order", {
  method: "POST",
  headers: { "X-API-Key": process.env.TTM_API_KEY },
  body: JSON.stringify({ token_address: "0xMOON", amount: 100 })
});`} />
      </div>
    </div>
  );
}

function AuditLogPage({ events }: { events: AuditEvent[] }) {
  const actorCount = new Set(events.map((event) => `${event.actor_type}:${event.actor_id || "system"}`)).size;
  const resourceCount = new Set(events.map((event) => `${event.target_type}:${event.target_id || "-"}`)).size;
  const latestEvent = events.reduce<AuditEvent | null>((latest, event) => {
    if (!latest) return event;
    return new Date(event.created_at).getTime() > new Date(latest.created_at).getTime() ? event : latest;
  }, null);

  return (
    <SimplePage title="Audit Log" subtitle="Append-only event log for keys, payments, paper orders, and guardrail decisions.">
      <div className="audit-summary-strip" aria-label="Audit log summary">
        <div>
          <span>Events loaded</span>
          <strong>{events.length}</strong>
        </div>
        <div>
          <span>Actors</span>
          <strong>{actorCount}</strong>
        </div>
        <div>
          <span>Resources</span>
          <strong>{resourceCount}</strong>
        </div>
        <div>
          <span>Latest event</span>
          <strong>{latestEvent ? latestEvent.action.replaceAll(".", " ") : "none"}</strong>
        </div>
      </div>
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Resource</th><th>Detail</th></tr></thead>
          <tbody>
            {events.length === 0 ? (
              <tr>
                <td colSpan={5} className="empty-cell">No audit events yet for this workspace. Create a key, run a simulation, or approve a proposal to populate this tenant log.</td>
              </tr>
            ) : events.map((event) => (
              <tr key={event.id}>
                <td className="mono muted">{compactDate(event.created_at)}</td>
                <td><span className="chip neut">{event.action.replaceAll(".", " ")}</span></td>
                <td className="mono">{event.actor_type}:{event.actor_id || "system"}</td>
                <td className="mono muted">{event.target_type}:{event.target_id || "-"}</td>
                <td className="muted small">{event.ip || "internal"} {event.ua || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SimplePage>
  );
}

function DocsPage() {
  const setupRows = [
    ["1", "Criar conta ou entrar", "Use Comecar gratis ou Entrar. Depois do login voce cai no Guia de Setup."],
    ["2", "Salvar Gemini API key", "Abra Settings, cole a chave, mantenha o modelo padrao e clique em Save Gemini key."],
    ["3", "Abrir Bot Modes", "Revise Mode 1 paper auto, Mode 2 com aprovacao e Mode 3 bloqueado."],
    ["4", "Rodar scanner", "Use o scanner para gerar propostas e simulacoes sem enviar ordem real para mainnet."],
    ["5", "Auditar eventos", "Use Audit Log para conferir API keys, simulacoes, aprovacoes e guardrails."],
  ];
  const endpointRows = [
    ["POST", "/api/v1/simulate/order", "Submit a paper trade", "key"],
    ["GET", "/api/v1/saas/dashboard", "Account dashboard", "jwt"],
    ["GET", "/api/v1/saas/account", "Org and plan info", "jwt"],
    ["GET", "/api/payments/catalog", "Pay-per-call catalog", "public"],
    ["POST", "/api/payments/intent", "Create payment requirement", "jwt"],
    ["GET", "/api/arc/jobs", "Scoped Arc proof history", "jwt"],
    ["GET", "/health", "Runtime health", "public"],
  ];
  const [endpointFilter, setEndpointFilter] = useState("");
  const normalizedFilter = endpointFilter.trim().toLowerCase();
  const filteredEndpointRows = normalizedFilter
    ? endpointRows.filter((row) => row.join(" ").toLowerCase().includes(normalizedFilter))
    : endpointRows;

  return (
    <SimplePage title="Docs & SDK" subtitle="Guia operacional, quickstarts e contratos de API para usar a plataforma sem se perder.">
      <div className="card">
        <CardHead title="Primeira sessao recomendada" sub="Fluxo guiado para deixar sua conta pronta" badge="start here" />
        <div className="doc-steps">
          {setupRows.map(([step, title, body]) => (
            <div className="doc-step" key={step}>
              <span>{step}</span>
              <div>
                <strong>{title}</strong>
                <p>{body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
      <Divider label="Configuracao essencial" />
      <div className="three-grid">
        <div className="card">
          <CardHead title="Gemini API key" sub="Obrigatoria para o bot da sua conta" />
          <ul className="check-list">
            <li><Check size={14} /> Va em Settings.</li>
            <li><Check size={14} /> Cole a chave no campo Gemini API key.</li>
            <li><Check size={14} /> Salve e confira o hint Configured as.</li>
          </ul>
        </div>
        <div className="card">
          <CardHead title="Bot e copilot" sub="Sempre com controle humano neste ambiente" />
          <ul className="check-list">
            <li><Check size={14} /> Abra Bot Modes.</li>
            <li><Check size={14} /> Use Simulator ou Copilot.</li>
            <li><Check size={14} /> Aprove propostas somente depois de revisar.</li>
          </ul>
        </div>
        <div className="card">
          <CardHead title="Modo seguro" sub="O esperado hoje e paper only" />
          <ul className="check-list">
            <li><Check size={14} /> Ordens mainnet ficam bloqueadas.</li>
            <li><Check size={14} /> Paper Trading nao envia ordem real.</li>
            <li><Check size={14} /> Audit Log registra decisoes importantes.</li>
          </ul>
        </div>
      </div>
      <Divider label="SDK quickstarts" />
      <div className="three-grid">
        <CodeCard title="Python" code="pip install tothemoon" detail="Async-first client with typed response models." />
        <CodeCard title="TypeScript" code="npm i @ttm/sdk" detail="Browser and Node client with payment helpers." />
        <CodeCard title="REST" code="GET /docs" detail="OpenAPI schema generated from the live API." />
      </div>
      <Divider label="Legal drafts" />
      <div className="three-grid">
        <div className="card">
          <CardHead title="Terms of Service" sub="Attorney-review draft" />
          <p className="muted small">Draft terms cover accounts, acceptable use, billing, availability, paper-only defaults, and mainnet guardrails.</p>
        </div>
        <div className="card">
          <CardHead title="Privacy Policy" sub="Attorney-review draft" />
          <p className="muted small">Draft privacy language covers account data, credential metadata, usage, billing, telemetry, retention, and user rights.</p>
        </div>
        <div className="card">
          <CardHead title="Risk Disclosure" sub="Attorney-review draft" />
          <p className="muted small">Draft disclosure states simulations and copilot output are informational, not investment advice, and live trading remains disabled unless authorized.</p>
        </div>
      </div>
      <Divider label="Endpoint reference" />
      <div className="card flush">
        <div className="endpoint-filter">
          <label>
            Filter endpoint reference
            <input
              value={endpointFilter}
              onChange={(event) => setEndpointFilter(event.target.value)}
              placeholder="Search by path, method, auth, or description"
            />
          </label>
          <span>{filteredEndpointRows.length} of {endpointRows.length} endpoints</span>
        </div>
        <table className="table">
          <thead><tr><th>Method</th><th>Path</th><th>Description</th><th>Auth</th></tr></thead>
          <tbody>
            {filteredEndpointRows.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty-cell">No endpoints match this filter. Try a path like /health or auth like jwt.</td>
              </tr>
            ) : filteredEndpointRows.map(([method, path, desc, auth]) => (
              <tr key={path}>
                <td><span className={`chip ${method === "GET" ? "brand" : "plasma"}`}>{method}</span></td>
                <td className="mono bold">{path}</td>
                <td className="muted">{desc}</td>
                <td className="mono">{auth}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SimplePage>
  );
}

function SettingsPage({
  account,
  usage,
  health,
  activeApiKeys,
  invoices,
  geminiApiKey,
  geminiModel,
  onGeminiApiKey,
  onGeminiModel,
  onSaveGemini,
  onDeleteGemini,
  onCheckout,
  onSignOut,
  busy,
  checkoutPlan,
}: {
  account: Account;
  usage: Usage;
  health: Health;
  activeApiKeys: number;
  invoices: InvoiceSummary[];
  geminiApiKey: string;
  geminiModel: string;
  onGeminiApiKey: (value: string) => void;
  onGeminiModel: (value: string) => void;
  onSaveGemini: () => void;
  onDeleteGemini: () => void;
  onCheckout: (plan: "pro" | "enterprise") => void;
  onSignOut: () => void;
  busy: boolean;
  checkoutPlan: "pro" | "enterprise" | null;
}) {
  const geminiCredential = account.provider_credentials?.gemini;
  const geminiConfigured = Boolean(geminiCredential?.configured);
  const cleanGeminiKey = geminiApiKey.trim();
  const geminiKeyLooksMasked = looksLikeKeyHint(cleanGeminiKey);
  const geminiSaveDisabled = busy || !cleanGeminiKey || geminiKeyLooksMasked;
  const proPending = checkoutPlan === "pro";
  const enterprisePending = checkoutPlan === "enterprise";
  const invoiceTotal = invoices.reduce((total, invoice) => total + (invoice.amount || 0), 0);
  const monthlyRemaining = Math.max(0, usage.quota.simulate_order - usage.requests_month);
  const latestInvoice = invoices[0];
  const busyStatus = checkoutPlan
    ? `Opening ${checkoutPlan === "pro" ? "Pro" : "Enterprise"} checkout. Account actions are temporarily disabled.`
    : busy
      ? "Settings actions are temporarily disabled while data refreshes."
      : "Billing actions are ready.";
  return (
    <SimplePage title="Settings" subtitle="Organization, bot credentials, billing tier, runtime policy, and quota controls.">
      <div className="two-grid">
        <div className="card">
          <CardHead
            title="Gemini bot credential"
            sub={geminiConfigured ? `Configured as ${geminiCredential?.key_hint}` : "Required for the account bot to answer with your Gemini key"}
            badge={geminiConfigured ? "ready" : "missing"}
          />
          <div className="field-grid">
            <label>
              Gemini API key
              <input
                value={geminiApiKey}
                onChange={(event) => onGeminiApiKey(event.target.value)}
                placeholder={geminiConfigured ? "Paste a new key to rotate" : "AIza..."}
                type="password"
                autoComplete="off"
                aria-describedby="gemini-key-secret-hint"
              />
            </label>
            <label>
              Model
              <input
                value={geminiModel}
                onChange={(event) => onGeminiModel(event.target.value)}
                placeholder="gemini-3.1-pro-preview"
              />
            </label>
            <p id="gemini-key-secret-hint" className="field-hint">
              Paste the full Google AI Studio key only when saving. After storage, this screen shows only the masked key hint and never echoes the full secret.
            </p>
            {geminiKeyLooksMasked ? (
              <p className="field-hint danger" role="alert">
                This looks like a masked key hint. Paste the complete Gemini API key before saving.
              </p>
            ) : null}
          </div>
          <div className="plan-actions">
            <button
              className="btn primary"
              onClick={onSaveGemini}
              disabled={geminiSaveDisabled}
              aria-disabled={geminiSaveDisabled}
              aria-label={geminiKeyLooksMasked ? "Save Gemini key disabled because the value is masked" : "Save Gemini key"}
            >
              <KeyRound size={13} />
              Save Gemini key
            </button>
            <button className="btn danger" onClick={onDeleteGemini} disabled={busy || !geminiConfigured}>
              Remove key
            </button>
          </div>
          <div className="credential-summary" aria-label="Gemini credential summary">
            <Detail label="Status" value={geminiConfigured ? "configured" : "missing"} />
            <Detail label="Key hint" value={geminiCredential?.key_hint || "full key hidden"} />
            <Detail label="Model" value={geminiCredential?.model || geminiModel || "gemini-3.1-pro-preview"} />
            <Detail label="Updated" value={compactDate(geminiCredential?.updated_at)} />
          </div>
          <div className="muted small">
            A chave fica criptografada no backend e o valor completo nao volta para o navegador.
          </div>
        </div>
        <div className="card">
          <CardHead title="Organization" sub={`${account.email} - org ${account.org_id}`} />
          <div className="field-grid">
            <label>Name<input value={account.organization} readOnly /></label>
            <label>Plan<input value={account.plan} readOnly /></label>
            <label>Mode<input value={health.mode} readOnly /></label>
            <label>Order submission<input value={health.orderSubmissionEnabled ? "enabled" : "blocked"} readOnly /></label>
          </div>
          <button className="btn danger" onClick={onSignOut} disabled={busy} aria-disabled={busy} aria-label={busy ? "Sign out disabled while billing or data refresh is pending" : "Sign out"}><LogOut size={13} /> Sign out</button>
        </div>
        <div className="card" aria-busy={Boolean(checkoutPlan)}>
          <CardHead title="Quota this month" sub={`${usage.requests_month} requests used`} />
          <QuotaRow label="Simulate calls" used={usage.requests_month} total={usage.quota.simulate_order} color="var(--brand)" />
          <QuotaRow label="Audit scans" used={Math.round(usage.requests_month / 3)} total={usage.quota.token_audit} color="var(--plasma)" />
          <QuotaRow label="Active keys" used={activeApiKeys} total={usage.quota.active_api_keys} color="var(--signal)" />
          <div className="billing-summary" aria-label="Billing invoice summary">
            <Detail label="Invoices" value={String(invoices.length)} />
            <Detail label="Invoice total" value={money(invoiceTotal)} />
            <Detail label="Plan" value={usage.plan} />
            <Detail label="Remaining calls" value={monthlyRemaining.toLocaleString()} />
            <Detail label="Latest" value={latestInvoice?.id || "none"} />
            <Detail label="Latest amount" value={latestInvoice ? money(latestInvoice.amount || 0) : "$0.00"} />
          </div>
          <div aria-label="Latest billing invoice">
            {latestInvoice ? (
              <div className="list-row">
                <div>
                  <strong>{latestInvoice.id || "Latest invoice"}</strong>
                  <span>{money(latestInvoice.amount || 0)} billed to this workspace</span>
                </div>
                {latestInvoice.url ? (
                  <a className="btn sm" href={latestInvoice.url} target="_blank" rel="noreferrer">
                    Open invoice
                  </a>
                ) : (
                  <span className="chip neut">No link</span>
                )}
              </div>
            ) : (
              <div className="empty-list">No invoices yet for this workspace. Checkout receipts will appear here after billing events sync.</div>
            )}
          </div>
          <p className="muted" role="status" aria-live="polite">{busyStatus}</p>
          <div className="plan-actions" aria-label="Billing checkout actions">
            <button className="btn primary" disabled={busy} aria-disabled={busy} aria-label={proPending ? "Opening Pro checkout" : "Select Pro billing plan"} onClick={() => onCheckout("pro")}>{proPending ? "Opening Pro..." : "Select Pro"}</button>
            <button className="btn" disabled={busy} aria-disabled={busy} aria-label={enterprisePending ? "Opening Enterprise checkout" : "Select Enterprise billing plan"} onClick={() => onCheckout("enterprise")}>{enterprisePending ? "Opening Enterprise..." : "Select Enterprise"}</button>
          </div>
        </div>
      </div>
    </SimplePage>
  );
}

function LifecyclePanel({
  artifacts,
  selectedArtifact,
  selectedArtifactId,
  activePayment,
  nexusJob,
  activeArcProof,
  nextLifecycleAction,
  busy,
  onSelectedArtifact,
  onCreatePayment,
  onVerifyPayment,
  onUnlockDelivery,
}: {
  artifacts: BillableArtifact[];
  selectedArtifact: BillableArtifact | null;
  selectedArtifactId: string;
  activePayment: PaymentIntentRecord | null;
  nexusJob: NexusJob | null;
  activeArcProof: ArcJobProof | null;
  nextLifecycleAction: string;
  busy: boolean;
  onSelectedArtifact: (value: string) => void;
  onCreatePayment: () => void;
  onVerifyPayment: () => void;
  onUnlockDelivery: () => void;
}) {
  const deliveryUnlocked = Boolean(activePayment?.download_url || nexusJob?.state === "DELIVERED");
  const steps = [
    { label: "1 - Price", complete: Boolean(selectedArtifact), helper: selectedArtifact ? `${selectedArtifact.name} selected` : "Choose an artifact" },
    { label: "2 - Escrow", complete: Boolean(activePayment), helper: activePayment?.status || "Create requirement" },
    { label: "3 - Verify", complete: activePayment?.status === "verified", helper: activePayment?.settlement_status || "Waiting" },
    { label: "4 - Deliver", complete: deliveryUnlocked, helper: deliveryUnlocked ? "Artifact unlocked" : "Locked" },
  ];

  return (
    <section className="card lifecycle-panel" aria-labelledby="paid-agent-lifecycle-title">
      <div className="card-head">
        <div>
          <span className="card-sub">Arc testnet metadata</span>
          <h2 id="paid-agent-lifecycle-title" className="card-title big">Paid Agent Lifecycle</h2>
        </div>
        <span className="chip warn">No mainnet execution</span>
      </div>
      <div className="lifecycle-next">
        <span>Next action</span>
        <strong>{nextLifecycleAction}</strong>
      </div>
      <ol className="lifecycle-steps" aria-label="Paid lifecycle progress">
        {steps.map((step) => (
          <li className={step.complete ? "complete" : ""} key={step.label}>
            <strong>{step.label}</strong>
            <span>{step.helper}</span>
          </li>
        ))}
      </ol>
      <div className="lifecycle-grid">
        <div className="artifact-options" role="radiogroup" aria-label="Priced artifact options">
          {artifacts.length === 0 ? (
            <div className="empty-list" role="status">No priced artifacts are available for this workspace yet. Refresh after the catalog sync completes before creating a payment requirement.</div>
          ) : artifacts.map((artifact) => (
            <button
              className={`artifact-card ${artifact.id === selectedArtifactId ? "selected" : ""}`}
              key={artifact.id}
              onClick={() => onSelectedArtifact(artifact.id)}
              role="radio"
              aria-checked={artifact.id === selectedArtifactId}
            >
              <span>{artifact.name}</span>
              <strong>{money(artifact.price_usd)} USDC</strong>
              <small>{artifact.description}</small>
            </button>
          ))}
        </div>
        <div className="lifecycle-details">
          <div className="lifecycle-actions">
            <button className="btn primary" disabled={busy || !selectedArtifact} onClick={onCreatePayment}><CreditCard size={13} /> Create payment requirement</button>
            <button className="btn" disabled={busy || !activePayment || activePayment.status === "verified"} onClick={onVerifyPayment}><Check size={13} /> Verify payment</button>
            <button className="btn" disabled={busy || !activePayment || activePayment.status !== "verified" || deliveryUnlocked} onClick={onUnlockDelivery}><Download size={13} /> Unlock delivery</button>
          </div>
          <div className="payment-safety-strip" aria-label="Payment safety summary">
            <div>
              <span>Requirement</span>
              <strong>{activePayment ? activePayment.status : "not issued"}</strong>
            </div>
            <div>
              <span>Settlement</span>
              <strong>{activePayment?.settlement_status || "pending"}</strong>
            </div>
            <div>
              <span>Job state</span>
              <strong>{nexusJob?.state || "waiting"}</strong>
            </div>
            <div>
              <span>Delivery</span>
              <strong>{deliveryUnlocked ? "unlocked" : "locked"}</strong>
            </div>
          </div>
          <div className="gate-grid" aria-label="Lifecycle gates">
            <Gate label="Payment" value={activePayment?.status || "required"} active={Boolean(activePayment)} />
            <Gate label="Job" value={nexusJob?.state || "waiting"} active={Boolean(nexusJob)} />
            <Gate label="Review" value={nexusJob?.state === "DELIVERED" ? "passed" : nexusJob?.state === "REVIEW_PENDING" ? "pending" : "not ready"} active={nexusJob?.state === "REVIEW_PENDING" || nexusJob?.state === "DELIVERED"} />
            <Gate label="Delivery" value={deliveryUnlocked ? "unlocked" : "locked"} active={deliveryUnlocked} />
          </div>
          <div className="proof-grid">
            <Detail label="Artifact" value={activePayment?.artifact_name || selectedArtifact?.name || "Select an artifact"} />
            <Detail label="Payment ID" value={shortId(activePayment?.payment_id)} />
            <Detail label="Settlement" value={activePayment?.settlement_status || "pending"} />
            <Detail label="Legacy job" value={shortId(nexusJob?.id || activePayment?.job_id)} />
            <Detail label="Arc Proof" value={shortId(activeArcProof?.proof_hash)} />
            <Detail label="Arc Write" value={activeArcProof?.onchain_write_status || "stub unavailable"} />
          </div>
        </div>
      </div>
    </section>
  );
}

function SimulationCard({
  tokenAddress,
  amount,
  busy,
  onTokenAddress,
  onAmount,
  onSimulate,
}: {
  tokenAddress: string;
  amount: number;
  busy: boolean;
  onTokenAddress: (value: string) => void;
  onAmount: (value: number) => void;
  onSimulate: () => void;
}) {
  return (
    <div className="card">
      <CardHead title="Simulation" sub="/api/v1/simulate/order" badge="paper" />
      <div className="field-grid">
        <label>Token<input value={tokenAddress} onChange={(event) => onTokenAddress(event.target.value)} /></label>
        <label>Amount<input type="number" min={1} value={amount} onChange={(event) => onAmount(Number(event.target.value))} /></label>
      </div>
      <button className="btn primary" disabled={busy} onClick={onSimulate}><Play size={13} /> Run</button>
    </div>
  );
}

function CopilotCard({ proposals, busy, onApprove }: { proposals: CopilotProposal[]; busy: boolean; onApprove: (id: number) => void }) {
  return (
    <div className="card">
      <CardHead title="Copilot queue" sub="Human approval before execution" badge={`${proposals.length} queued`} />
      <div className="list">
        {proposals.length === 0 ? (
          <div className="empty-list" role="status">No copilot proposals yet. Run a paper simulation or wait for the scanner to queue a tenant-scoped recommendation.</div>
        ) : proposals.slice(0, 4).map((proposal) => (
          <div className="list-row" key={proposal.id}>
            <div>
              <strong>{proposal.symbol || proposal.token_address} <span className={`chip ${proposalStatusTone(proposal.status)}`}>{proposalStatusLabel(proposal.status)}</span></strong>
              <span>{proposal.rationale}</span>
              {proposalStatusFeedback(proposal) ? <span>{proposalStatusFeedback(proposal)}</span> : null}
            </div>
            {isPendingProposalStatus(proposal.status) ? (
              <button className="btn sm primary" disabled={busy} onClick={() => onApprove(proposal.id)}>
                Approve proposal
              </button>
            ) : (
              <span className="muted small">{proposalActionLabel(proposal)}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function NoticeBanner({ message, mode, busy, onRetry }: { message: string; mode: DataMode; busy: boolean; onRetry: () => void }) {
  return (
    <section className="status-banner" role="alert">
      <AlertTriangle size={18} />
      <div>
        <strong>{mode === "degraded" ? "Data sync degraded" : "Data refresh failed"}</strong>
        <span>{message}</span>
      </div>
      <button className="btn" disabled={busy} onClick={onRetry}>{busy ? "Checking..." : "Retry"}</button>
    </section>
  );
}

function KPI({ label, value, unit, delta, spark, sparkColor = "var(--brand)", emptyLabel }: { label: string; value: string; unit?: string; delta?: string; spark?: number[]; sparkColor?: string; emptyLabel?: string }) {
  const sparkValues = spark && spark.length > 1 ? spark : null;

  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}{unit ? <span className="unit">{unit}</span> : null}</div>
      {delta ? <div className="kpi-delta pos">{delta}</div> : null}
      {sparkValues ? <Sparkline values={sparkValues} color={sparkColor} /> : emptyLabel ? <div className="kpi-empty">{emptyLabel}</div> : null}
    </div>
  );
}

function Sparkline({ values, color }: { values: number[]; color: string }) {
  const path = sparkPath(values);
  return (
    <svg className="spark" viewBox="0 0 120 36" preserveAspectRatio="none" aria-hidden="true">
      <path d={path} fill="none" stroke={color} strokeWidth="1.6" />
      <path d={`${path} L 120 36 L 0 36 Z`} fill={color} opacity="0.08" />
    </svg>
  );
}

function EquityChart({ points, loading = false }: { points: number[]; loading?: boolean }) {
  const data = points.length > 1 ? points : Array.from({ length: 12 }, () => 0);
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const scaled = data.map((value) => 180 - ((value - min) / range) * 130);
  const width = 600 / (scaled.length - 1);
  const d = scaled.map((y, index) => `${index === 0 ? "M" : "L"} ${index * width} ${y}`).join(" ");
  return (
    <svg viewBox="0 0 600 210" className={`equity-chart ${loading ? "loading" : ""}`} aria-label="Equity curve">
      <defs>
        <linearGradient id="eqGrad" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#5BE9B9" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#5BE9B9" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[40, 80, 120, 160].map((y) => <line key={y} x1="0" x2="600" y1={y} y2={y} stroke="#1E2330" strokeDasharray="2 4" />)}
      <path d={`${d} L 600 210 L 0 210 Z`} fill="url(#eqGrad)" />
      <path d={d} fill="none" stroke="#5BE9B9" strokeWidth="2" />
      <circle cx={(scaled.length - 1) * width} cy={scaled[scaled.length - 1]} r="5" fill="#5BE9B9" />
    </svg>
  );
}

function StrategyMix({
  dashboard,
  usage,
  proposals,
}: {
  dashboard: Dashboard;
  usage: Usage;
  proposals: CopilotProposal[];
}) {
  const trades = dashboard.total_simulated_trades || 0;
  const requests = usage.requests_month || 0;
  const queuedSignals = proposals.length;
  const total = Math.max(1, trades + requests + queuedSignals);
  const strategies = [
    {
      name: "paper-simulations",
      desc: `${trades} simulated trades recorded in this workspace`,
      alloc: Math.round((trades / total) * 100),
      pnl: money((dashboard.realized_pnl_usd || 0) + (dashboard.unrealized_pnl_usd || 0)),
      color: "var(--brand)",
    },
    {
      name: "api-requests",
      desc: `${requests} authenticated requests this month`,
      alloc: Math.round((requests / total) * 100),
      pnl: `${usage.requests_today} today`,
      color: "var(--plasma)",
    },
    {
      name: "agent-proposals",
      desc: `${queuedSignals} scanner proposals in this workspace`,
      alloc: Math.max(queuedSignals > 0 ? 8 : 0, Math.round((queuedSignals / total) * 100)),
      pnl: `${proposals.filter((proposal) => proposal.status === "executed").length} executed`,
      color: "var(--signal)",
    },
  ];

  return (
    <div className="col gap-16">
      {strategies.map((strategy) => (
        <div className="col gap-4" key={strategy.name}>
          <div className="row spread">
            <span className="mono bold small">{strategy.name}</span>
            <span className="mono small pos">{strategy.pnl}</span>
          </div>
          <div className="muted small">{strategy.desc}</div>
          <div className="bar"><span style={{ width: `${strategy.alloc}%`, background: strategy.color }} /></div>
        </div>
      ))}
    </div>
  );
}

function AuditRing({ score }: { score: number }) {
  const radius = 72;
  const circumference = 2 * Math.PI * radius;
  const dash = (score / 100) * circumference;
  return (
    <svg className="audit-ring" viewBox="0 0 168 168" aria-label={`Audit score ${score} out of 100`}>
      <defs>
        <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#5BE9B9" />
          <stop offset="100%" stopColor="#34C896" />
        </linearGradient>
      </defs>
      <circle cx="84" cy="84" r={radius} fill="none" stroke="#1E2330" strokeWidth="10" />
      <circle cx="84" cy="84" r={radius} fill="none" stroke="url(#ringGrad)" strokeWidth="10" strokeLinecap="round" strokeDasharray={`${dash} ${circumference}`} transform="rotate(-90 84 84)" />
      <text x="84" y="88" textAnchor="middle" fill="#F4F5F7" fontSize="38" fontWeight="600">{score}</text>
      <text x="84" y="108" textAnchor="middle" fill="#8B92A3" fontSize="10">/ 100</text>
    </svg>
  );
}

function Gate({ label, value, active }: { label: string; value: string; active?: boolean }) {
  return (
    <div className={`gate ${active ? "active" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CardHead({ title, sub, badge }: { title: string; sub?: string; badge?: string }) {
  return (
    <div className="card-head">
      <div className="col">
        <span className="card-title">{title}</span>
        {sub ? <span className="card-sub">{sub}</span> : null}
      </div>
      {badge ? <span className="chip neut">{badge}</span> : null}
    </div>
  );
}

function PageHead({ title, subtitle, actions }: { title: string; subtitle: string; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        <div className="page-subtitle">{subtitle}</div>
      </div>
      {actions ? <div className="row gap-8 wrap">{actions}</div> : null}
    </div>
  );
}

function SimplePage({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="page">
      <PageHead title={title} subtitle={subtitle} />
      {children}
    </div>
  );
}

function Divider({ label }: { label: string }) {
  return (
    <div className="divider">
      <span className="divider-label">{label}</span>
      <div className="divider-line" />
    </div>
  );
}

function CodeCard({ title, code, detail }: { title: string; code: string; detail?: string }) {
  return (
    <div className="card">
      <CardHead title={title} sub={detail} />
      <pre className="code">{code}</pre>
    </div>
  );
}

function ToggleRow({ label, value, active }: { label: string; value: string; active: boolean }) {
  return (
    <div className="toggle-row">
      <div>
        <strong>{label}</strong>
        <span>{value}</span>
      </div>
      <span className={`switch ${active ? "on" : ""}`} />
    </div>
  );
}

function DetailList({ rows }: { rows: Array<[string, string]> }) {
  return (
    <div className="detail-list">
      {rows.map(([label, value]) => (
        <div className="row spread" key={label}>
          <span className="muted">{label}</span>
          <strong className="mono">{value}</strong>
        </div>
      ))}
    </div>
  );
}

function QuotaRow({ label, used, total, color }: { label: string; used: number; total: number; color: string }) {
  const pct = Math.min(100, Math.round((used / Math.max(1, total)) * 100));
  return (
    <div className="quota-row">
      <div className="row spread">
        <span>{label}</span>
        <span className="mono muted">{used.toLocaleString()} / {total.toLocaleString()}</span>
      </div>
      <div className="bar"><span style={{ width: `${pct}%`, background: color }} /></div>
    </div>
  );
}

function sparkPath(values: number[], w = 120, h = 36) {
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1);
  return values.map((value, index) => `${index === 0 ? "M" : "L"} ${index * step} ${h - ((value - min) / range) * h}`).join(" ");
}

function recentPaperRows(proposals: CopilotProposal[]) {
  return proposals.slice(0, 8).map((proposal) => ({
    sym: proposal.symbol || shortId(proposal.token_address),
    side: proposal.side,
    size: money(proposal.amount_usd),
    entry: `${proposalScorePct(proposal.score)}/100`,
    exit: proposal.mode === "paper" ? "paper" : "approval",
    pnl: proposal.status === "executed" ? "+simulated" : "+queued",
    strategy: proposal.chain,
    status: proposal.status === "executed" ? "CLOSED" : proposal.status === "failed" ? "STOP" : "OPEN",
  }));
}

function auditRows(pillarId: string) {
  const rows: Record<string, Array<{ label: string; evidence: string; status: "PASS" | "TODO" | "BLOCKER" }>> = {
    saas: [
      { label: "Tenant-scoped users", evidence: "/api/v1/saas/account", status: "PASS" },
      { label: "Hashed API keys", evidence: "auth.py key hashing", status: "PASS" },
      { label: "Role-based members", evidence: "member roles beyond owner", status: "TODO" },
    ],
    paper: [
      { label: "Simulated trade persistence", evidence: "/simulate/order", status: "PASS" },
      { label: "Fees and slippage modeled", evidence: "simulation engine", status: "PASS" },
      { label: "MEV loss model", evidence: "probabilistic penalty", status: "TODO" },
    ],
    scanner: [
      { label: "Market scanner", evidence: "/api/v1/copilot/scan", status: "PASS" },
      { label: "Honeypot block", evidence: "security consensus", status: "PASS" },
      { label: "Strategy learning loop", evidence: "backtest feedback", status: "TODO" },
    ],
    bot: [
      { label: "Paper-only policy", evidence: "orderSubmissionEnabled=false", status: "PASS" },
      { label: "Copilot queue", evidence: "/copilot/proposals", status: "PASS" },
      { label: "Autonomous hard caps", evidence: "runtime spend limits", status: "TODO" },
    ],
    security: [
      { label: "Security headers", evidence: "observability middleware", status: "PASS" },
      { label: "Audit log", evidence: "/saas/audit-log", status: "PASS" },
      { label: "Contract scanner", evidence: "real provider integration", status: "BLOCKER" },
    ],
    obs: [
      { label: "Health endpoint", evidence: "/health", status: "PASS" },
      { label: "Ready endpoint", evidence: "/ready", status: "PASS" },
      { label: "Alert routing", evidence: "SLO alert policy", status: "TODO" },
    ],
    billing: [
      { label: "Quota table", evidence: "/saas/usage", status: "PASS" },
      { label: "Checkout session", evidence: "/billing/checkout", status: "PASS" },
      { label: "Meter rollups", evidence: "hourly aggregation", status: "TODO" },
    ],
    infra: [
      { label: "Docker VM runtime", evidence: "docker-compose.vm.yml", status: "PASS" },
      { label: "Migration gate", evidence: "alembic upgrade head", status: "PASS" },
      { label: "Multi-region failover", evidence: "single VM today", status: "TODO" },
    ],
  };
  return rows[pillarId] || rows.saas;
}

function navGroup(view: View) {
  if (["guide", "overview", "audit", "paper", "bot", "market"].includes(view)) return "Trade";
  if (["pay", "keys", "log"].includes(view)) return "Build";
  return "Account";
}

function viewTitle(view: View) {
  const titles: Record<View, string> = {
    guide: "Guia de Setup",
    overview: "Overview",
    audit: "Production Audit",
    paper: "Paper Trading",
    bot: "Bot Modes",
    market: "Market Data",
    pay: "Legacy Lab",
    keys: "API Keys",
    log: "Audit Log",
    docs: "Docs & SDK",
    settings: "Settings",
  };
  return titles[view];
}

export default App;
