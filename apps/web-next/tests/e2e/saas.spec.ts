import { expect, type Page, test } from "@playwright/test";

const account = {
  email: "synthetic-user-2@example.com",
  org_id: 1,
  organization: "E2E Org",
  plan: "free",
  created_at: "2026-05-02T12:00:00.000Z",
  real_mode_enabled: false,
  real_mode_approved_at: null,
  real_mode_daily_limit_usd: 50,
  bot_consecutive_failures: 0,
};

const apiKeys = [
  {
    id: 7,
    org_id: 1,
    name: "default",
    prefix: "ttm_sk_test",
    scopes: "simulate",
    last_used_at: null,
    revoked_at: null,
    created_at: "2026-05-02T12:00:00.000Z",
  },
];

const proposal = {
  id: 11,
  org_id: 1,
  token_address: "0xMOON",
  chain: "base",
  symbol: "MOON",
  side: "BUY",
  amount_usd: 100,
  score: 0.91,
  rationale: "High conviction test proposal",
  mode: "paper",
  status: "pending",
  execution_payload: null,
  created_at: "2026-05-02T12:00:00.000Z",
  updated_at: "2026-05-02T12:00:00.000Z",
  approved_at: null,
};

type ProposalFixture = Omit<typeof proposal, "approved_at" | "execution_payload" | "mode" | "status"> & {
  approved_at: string | null;
  execution_payload: Record<string, unknown> | null;
  mode: string;
  status: string;
};

const artifacts = [
  {
    id: "artifact_delivery_packet",
    name: "Delivery Packet",
    description: "Reviewed delivery packet with execution summary and unlock metadata.",
    price_usd: 0.001,
    type: "delivery_packet",
  },
  {
    id: "artifact_review_bundle",
    name: "Review Bundle",
    description: "Reviewer-approved evidence bundle with settlement and quality checkpoints.",
    price_usd: 0.005,
    type: "review_bundle",
  },
];

type PaymentFixture = {
  payment_id: string;
  artifact_id: string;
  artifact_name: string;
  artifact_type: string;
  amount_usd: number;
  buyer_address: string;
  deposit_address: string;
  job_id: string;
  status: string;
  settlement_status: string;
  reason: string | null;
  tx_hash: string | null;
  circle_transaction_id: string | null;
  executed: boolean;
  download_url: string | null;
  execution_message: string | null;
  created_at: string;
  updated_at: string;
};

type NexusJobFixture = {
  id: string;
  description: string;
  state: string;
  payment_id: string;
  artifact_id: string;
  artifact_type: string;
  amount_usdc: number;
  buyer_address: string;
  settlement_status: string;
  tx_hash: string | null;
  download_url: string | null;
  history: Array<{ state: string; reason: string; at: string }>;
};

type ProviderCredentialFixture = {
  configured: boolean;
  provider: string;
  key_hint?: string;
  model?: string;
  created_at?: string;
  updated_at?: string;
};

const pendingPayment: PaymentFixture = {
  payment_id: "pay_demo_1234567890",
  artifact_id: "artifact_delivery_packet",
  artifact_name: "Delivery Packet",
  artifact_type: "delivery_packet",
  amount_usd: 0.001,
  buyer_address: "0x0000000000000000000000000000000000000003",
  deposit_address: "0xMockDepositAddressForTestnetOnly",
  job_id: "nexus-pay-demo",
  status: "pending",
  settlement_status: "PENDING",
  reason: null,
  tx_hash: null,
  circle_transaction_id: null,
  executed: false,
  download_url: null,
  execution_message: null,
  created_at: "2026-05-02T12:00:00.000Z",
  updated_at: "2026-05-02T12:00:00.000Z",
};

const paidPayment = {
  ...pendingPayment,
  status: "verified",
  settlement_status: "SETTLED",
  tx_hash: "0xMockTransactionHash",
  updated_at: "2026-05-02T12:01:00.000Z",
};

const deliveredPayment = {
  ...paidPayment,
  executed: true,
  download_url: "https://example.test/delivery-packet.json",
  execution_message: "Delivery unlocked after review gate.",
};

const nexusJob: NexusJobFixture = {
  id: "nexus-pay-demo",
  description: "Paid work unit for Delivery Packet",
  state: "REQUESTED",
  payment_id: pendingPayment.payment_id,
  artifact_id: pendingPayment.artifact_id,
  artifact_type: pendingPayment.artifact_type,
  amount_usdc: pendingPayment.amount_usd,
  buyer_address: pendingPayment.buyer_address,
  settlement_status: "PENDING",
  tx_hash: null,
  download_url: null,
  history: [{ state: "REQUESTED", reason: "Payment intent created", at: "2026-05-02T12:00:00.000Z" }],
};

const arcProof = {
  job_id: "job_arc_1_1777723260",
  task_id: "work-unit-demo",
  agent_id: "agent-demo",
  status: "escrowed",
  proof_hash: "0x0000000000000000000000000000000000000000000000000000000000000004",
  timestamp: "2026-05-02T12:01:00.000Z",
  metadata: { source: "paid_work_unit", onchain_write: "stubbed" },
  work_unit_id: "work-unit-demo",
  payment_id: pendingPayment.payment_id,
  payment_status: "verified",
  settlement_status: "SETTLED",
  tx_hash: "0xMockTransactionHash",
  onchain_write_status: "stubbed",
};

const defaultHealth = {
  ok: true,
  app: "ToTheMoonTokens",
  mode: "paper",
  orderSubmissionEnabled: false,
  wallet_provider: "circle",
  wallet_set_id: "test-wallet-set",
  wallets_loaded: true,
  settlement_auth_mode: "manual",
  autonomous_payments_enabled: false,
  providers: {},
};

async function mockSaasApi(page: Page) {
  let paymentOrder: PaymentFixture = pendingPayment;
  let currentNexusJob: NexusJobFixture = nexusJob;
  let geminiCredential: ProviderCredentialFixture = { configured: false, provider: "gemini" };
  let proposalList: ProposalFixture[] = [proposal];
  await page.route("**/health", async (route) => {
    await route.fulfill({ json: defaultHealth });
  });
  await page.route("**/api/v1/auth/login", async (route) => {
    await route.fulfill({ json: { access_token: "jwt-test", token_type: "bearer" } });
  });
  await page.route("**/api/v1/auth/signup", async (route) => {
    await route.fulfill({ status: 201, json: { id: 1, email: account.email, org_id: account.org_id, plan: account.plan } });
  });
  await page.route("**/api/v1/saas/account", async (route) => {
    await route.fulfill({ json: { ...account, provider_credentials: { gemini: geminiCredential } } });
  });
  await page.route("**/api/v1/saas/dashboard", async (route) => {
    await route.fulfill({
      json: {
        email: account.email,
        org_id: account.org_id,
        plan: account.plan,
        requests_this_month: 12,
        simulated_volume_usd: 100,
        active_api_keys: 1,
        total_simulated_trades: 1,
        total_volume_usd: 100,
        total_fees_usd: 8,
        realized_pnl_usd: 0,
        unrealized_pnl_usd: 0,
        last_30_days_chart_points: [],
      },
    });
  });
  await page.route("**/api/v1/saas/usage", async (route) => {
    await route.fulfill({
      json: {
        requests_today: 3,
        requests_month: 12,
        plan: account.plan,
        quota: { simulate_order: 1000, token_audit: 100, active_api_keys: 3 },
      },
    });
  });
  await page.route("**/api/v1/saas/api-keys", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ json: { id: 8, org_id: 1, name: "qa-key", prefix: "ttm_sk_new", plaintext: "ttm_sk_live_created" } });
      return;
    }
    await route.fulfill({ json: apiKeys });
  });
  await page.route("**/api/v1/saas/api-keys/7", async (route) => {
    await route.fulfill({ json: { id: 7, revoked_at: "2026-05-02T12:30:00.000Z" } });
  });
  await page.route("**/api/v1/saas/provider-credentials/gemini", async (route) => {
    if (route.request().method() === "DELETE") {
      geminiCredential = { configured: false, provider: "gemini" };
      await route.fulfill({ json: { org_id: 1, credential: geminiCredential } });
      return;
    }
    geminiCredential = {
      configured: true,
      provider: "gemini",
      key_hint: "AIzaSy...7890",
      model: "gemini-3-flash-preview",
      created_at: "2026-05-02T12:00:00.000Z",
      updated_at: "2026-05-02T12:00:00.000Z",
    };
    await route.fulfill({ json: { org_id: 1, credential: geminiCredential } });
  });
  await page.route("**/api/v1/saas/audit-log", async (route) => {
    await route.fulfill({
      json: {
        org_id: 1,
        events: [
          {
            id: 99,
            org_id: 1,
            actor_id: 1,
            actor_type: "user",
            action: "api_key.created",
            target_type: "api_key",
            target_id: "7",
            ip: "127.0.0.1",
            ua: "playwright",
            before: null,
            after: null,
            created_at: "2026-05-02T12:00:00.000Z",
          },
        ],
        limit: 50,
        offset: 0,
      },
    });
  });
  await page.route("**/api/v1/saas/invoices", async (route) => {
    await route.fulfill({ json: { org_id: 1, invoices: [{ id: "inv_smoke", amount: 49, url: "https://billing.example.test/invoices/inv_smoke" }] } });
  });
  await page.route("**/api/v1/copilot/proposals", async (route) => {
    await route.fulfill({ json: { org_id: 1, proposals: proposalList } });
  });
  await page.route("**/api/v1/copilot/scan", async (route) => {
    const payload = route.request().postDataJSON() as { execution_mode?: "paper_auto" | "copilot" | "real_approval" };
    const isRealApproval = payload.execution_mode === "real_approval";
    const scannedProposal = {
      ...proposal,
      id: isRealApproval || payload.execution_mode === "copilot" ? 12 : 13,
      mode: isRealApproval ? "real" : "paper",
      status: payload.execution_mode === "paper_auto" ? "executed" : "pending",
      approved_at: payload.execution_mode === "paper_auto" ? "2026-05-02T12:31:00.000Z" : null,
      execution_payload: isRealApproval
        ? {
            manual_gate: "metamask_signature_required",
            wallet_address: "0x0000000000000000000000000000000000000005",
            wallet_chain_id: "0x2105",
            order_submission_enabled: false,
          }
        : null,
    };
    proposalList = [scannedProposal];
    await route.fulfill({
      status: 201,
      json: {
        org_id: 1,
        execution_mode: payload.execution_mode || "paper_auto",
        scanner_run: {
          id: 21,
          org_id: 1,
          query: "USDC",
          execution_mode: payload.execution_mode || "paper_auto",
          thresholds: {},
          candidate_count: 1,
          blocked_count: 0,
          queued_count: payload.execution_mode === "paper_auto" ? 0 : 1,
          paper_executed_count: payload.execution_mode === "paper_auto" ? 1 : 0,
          provider_readiness_summary: {},
          created_at: "2026-05-02T12:31:00.000Z",
          completed_at: "2026-05-02T12:31:01.000Z",
        },
        created: [scannedProposal],
        skipped: [],
      },
    });
  });
  await page.route("**/api/v1/saas/token-discovery-history**", async (route) => {
    await route.fulfill({ json: { org_id: 1, history: [], limit: 50, offset: 0 } });
  });
  await page.route("**/api/v1/copilot/proposals/*/approve", async (route) => {
    const isWalletApproval = Boolean(route.request().postData());
    const approvedProposal = {
      ...proposal,
      id: Number(route.request().url().match(/proposals\/(\d+)\/approve/)?.[1] || 11),
      mode: isWalletApproval ? "real" : "paper",
      status: isWalletApproval ? "approved_manual_required" : "approved",
      approved_at: "2026-05-02T12:30:00.000Z",
      execution_payload: isWalletApproval
        ? {
            manual_gate: "metamask_signature_required",
            manual_approval: { wallet_address: "0x0000000000000000000000000000000000000005", order_submission_enabled: false },
          }
        : null,
    };
    proposalList = [approvedProposal];
    await route.fulfill({
      json: {
        proposal: approvedProposal,
      },
    });
  });
  await page.route("**/api/v1/copilot/stream?once=true", async (route) => {
    await route.fulfill({ body: "" });
  });
  await page.route("**/api/v1/simulate/order", async (route) => {
    await route.fulfill({
      json: {
        status: "simulated",
        trade_id: 123,
        token_address: "0xMOON",
        side: "BUY",
        input_amount: 100,
        executed_price: 1,
        fees_paid: 1,
        slippage_paid: 0,
        token_tax_paid: 0,
        net_amount: 99,
        estimated_quantity: 99,
        realized_pnl_usd: null,
        source: "playwright",
        warning: "",
      },
    });
  });
  await page.route("**/api/v1/billing/checkout", async (route) => {
    await route.fulfill({ json: { url: "https://billing.example.test/session", org_id: 1, plan: "pro" } });
  });
  await page.route("**/api/payments/catalog", async (route) => {
    await route.fulfill({ json: artifacts });
  });
  await page.route("**/api/payments/orders", async (route) => {
    await route.fulfill({ json: [paymentOrder] });
  });
  await page.route("**/api/payments/intent", async (route) => {
    paymentOrder = pendingPayment;
    currentNexusJob = nexusJob;
    await route.fulfill({
      json: {
        payment_id: paymentOrder.payment_id,
        amount_usd: paymentOrder.amount_usd,
        currency: "USDC",
        deposit_address: paymentOrder.deposit_address,
        payment_requirement: { asset: "USDC", network: "arc_testnet", amount: "0.001" },
        status: paymentOrder.status,
        job_id: paymentOrder.job_id,
      },
    });
  });
  await page.route("**/api/payments/verify", async (route) => {
    paymentOrder = paidPayment;
    currentNexusJob = { ...nexusJob, state: "PAYMENT_UNLOCKED", settlement_status: "SETTLED", tx_hash: "0xMockTransactionHash" };
    await route.fulfill({ json: { payment_id: paymentOrder.payment_id, status: "verified", unlocked_artifact_id: null, settlement_status: "SETTLED", reason: null } });
  });
  await page.route("**/api/payments/execute", async (route) => {
    paymentOrder = deliveredPayment;
    currentNexusJob = { ...currentNexusJob, state: "DELIVERED", download_url: deliveredPayment.download_url };
    await route.fulfill({ json: { artifact_id: paymentOrder.artifact_id, status: "completed", message: "Delivery unlocked after review gate.", download_url: paymentOrder.download_url } });
  });
  await page.route("**/api/jobs/nexus-pay-demo", async (route) => {
    await route.fulfill({ json: currentNexusJob });
  });
  await page.route("**/api/arc/jobs?limit=5", async (route) => {
    await route.fulfill({ json: [arcProof] });
  });
  await page.route("**/api/agent/chat", async (route) => {
    const payload = route.request().postDataJSON() as { message?: string };
    await route.fulfill({
      json: {
        reply: `Recebi: ${payload.message || "sem mensagem"}`,
        events: [],
        orders: [paymentOrder],
        settlement_auth_mode: "manual",
        autonomous_payments_enabled: false,
      },
    });
  });
}

async function signIn(page: Page, mode: "Login" | "Signup" = "Signup") {
  if (mode === "Signup") {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: /Paper trading/ })).toBeVisible();
    await page.getByRole("button", { name: "Comecar gratis" }).first().click();
  } else {
    await page.goto("/login");
  }
  await page.getByPlaceholder("synthetic-user-1@example.com").fill(account.email);
  await page.getByPlaceholder("Password").fill("correct horse battery staple");
  await page.getByRole("button", { name: mode === "Signup" ? "Criar conta + abrir dashboard" : "Entrar" }).click();
  await expect(page.getByText(account.organization)).toBeVisible();
}

async function openAuthenticatedRoute(page: Page, path: string) {
  await page.addInitScript(() => {
    localStorage.setItem("ttm_jwt", "jwt-test");
  });
  await page.goto(path);
  await expect(page.getByText(account.organization)).toBeVisible();
}

async function openWorkspaceNav(page: Page, name: string | RegExp) {
  await page.getByLabel("Workspace navigation").getByRole("button", { name }).click();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.clear();
    sessionStorage.clear();
    Object.defineProperty(window, "ethereum", {
      configurable: true,
      value: {
        request: async ({ method }: { method: string }) => {
          if (method === "eth_requestAccounts") return ["0x0000000000000000000000000000000000000005"];
          if (method === "eth_chainId") return "0x2105";
          if (method === "personal_sign") return "0xsigned";
          return null;
        },
      },
    });
  });
  await mockSaasApi(page);
});

test("public root renders landing page and keeps dashboard gated", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /Paper trading/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Comecar gratis" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Overview" })).toHaveCount(0);

  await page.goto("/app");
  await expect(page.getByRole("heading", { name: /Paper trading/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Overview" })).toHaveCount(0);
});


test("public legal routes render compliance and LGPD consent surfaces", async ({ page }) => {
  await page.goto("/legal");
  await expect(page.getByRole("heading", { name: "Contratos publicos e consentimento LGPD." })).toBeVisible();
  await expect(page.getByText("Direitos LGPD")).toHaveCount(0);
  await expect(page.getByText("Attorney-review draft")).toBeVisible();

  await page.getByRole("button", { name: "Privacidade" }).click();
  await expect(page).toHaveURL(/\/privacy$/);
  await expect(page.getByRole("heading", { name: "Politica de privacidade e LGPD." })).toBeVisible();
  await expect(page.getByText("Direitos LGPD")).toBeVisible();

  await page.goto("/terms");
  await expect(page.getByRole("heading", { name: "Termos de uso do TTM." })).toBeVisible();
  await expect(page.getByText("Paper-only", { exact: true })).toBeVisible();

  await page.goto("/risk");
  await expect(page.getByRole("heading", { name: "Divulgacao de riscos." })).toBeVisible();
  await expect(page.getByText("Sem recomendacao")).toBeVisible();
});

test("signup shows legal consent disclosure before account creation", async ({ page }) => {
  await page.goto("/signup");

  await expect(page.getByRole("heading", { name: "Criar conta gratis." })).toBeVisible();
  await expect(page.getByText("Ao criar conta, voce aceita os Termos, a Politica de Privacidade/LGPD e a Divulgacao de Riscos.")).toBeVisible();
});

test("authenticated workspace routes support direct links and browser history", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("ttm_jwt", "jwt-test");
  });

  await page.goto("/app/settings");
  await expect(page).toHaveURL(/\/app\/settings$/);
  await expect(page).toHaveTitle("Settings | ToTheMoonTokens");
  await expect(page.getByText("Gemini bot credential")).toBeVisible();
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toHaveAttribute("aria-current", "page");

  await page.getByRole("button", { name: "API Keys" }).click();
  await expect(page).toHaveURL(/\/app\/api-keys$/);
  await expect(page).toHaveTitle("API Keys | ToTheMoonTokens");
  await expect(page.getByRole("button", { name: "API Keys" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "API Keys" })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/\/app\/settings$/);
  await expect(page).toHaveTitle("Settings | ToTheMoonTokens");
  await expect(page.getByText("Gemini bot credential")).toBeVisible();
});

test("unknown authenticated workspace routes canonicalize to setup", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("ttm_jwt", "jwt-test");
  });

  await page.goto("/app/not-a-real-section");
  await expect(page).toHaveURL(/\/app$/);
  await expect(page).toHaveTitle("Guia de Setup | ToTheMoonTokens");
  await expect(page.getByRole("heading", { name: "Guia de Setup" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Guia de Setup" })).toHaveAttribute("aria-current", "page");
});

test("skip link moves browser focus to authenticated workspace content", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("ttm_jwt", "jwt-test");
  });

  await page.goto("/app");
  const skipLink = page.getByRole("link", { name: "Skip to workspace content" });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.keyboard.press("Tab");
    if (await skipLink.evaluate((node) => document.activeElement === node)) break;
  }
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/#workspace-content$/);
  await expect(page.locator("#workspace-content")).toBeFocused();
});

test("authenticated public auth routes redirect to workspace setup", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("ttm_jwt", "jwt-test");
  });

  await page.goto("/login");
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByRole("heading", { name: "Guia de Setup" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();

  await page.goto("/signup");
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByRole("heading", { name: "Guia de Setup" })).toBeVisible();
});

test("signup creates an authenticated dashboard session", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async () => undefined },
    });
  });

  await signIn(page);

  await expect(page.getByRole("heading", { name: "Guia de Setup" })).toBeVisible();
  await expect(page.getByText("Checklist da conta")).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Progresso do setup da conta" })).toHaveAttribute("aria-valuenow", "2");
  await expect(page.getByRole("progressbar", { name: "Progresso do setup da conta" })).toHaveAttribute("aria-valuemax", "5");
  await expect(page.getByRole("progressbar", { name: "Progresso do setup da conta" })).toHaveAttribute(
    "aria-valuetext",
    "2 de 5 passos concluidos. Proximo passo: Configurar Gemini API key.",
  );
  await expect(page.getByLabel("Passos do onboarding").getByRole("listitem")).toHaveCount(5);
  await expect(page.locator('[aria-current="step"]')).toContainText("Configurar Gemini API key");
  await expect(page.locator('[aria-current="step"]')).toContainText("Status do passo 2: proximo passo.");
  await expect(page.locator("#setup-next-step-action")).toContainText("Proximo passo: Configurar Gemini API key.");
  await expect(page.getByRole("button", { name: "Abrir proximo passo: Configurar Gemini API key" })).toHaveAttribute("aria-describedby", "setup-next-step-action");
  await expect(page.getByRole("button", { name: "Continuar para Configurar Gemini API key" })).toHaveAttribute("aria-describedby", "setup-next-step-action");
  await expect(page.locator(".setup-summary")).toContainText("Proximo passo");
  await expect(page.locator(".setup-summary")).toContainText("Configurar Gemini API key");
  const nextAction = page.getByLabel("Proxima acao recomendada");
  await expect(nextAction).toContainText("Configurar Gemini API key");
  await expect(nextAction).toContainText("Cole sua Gemini API key em Settings");
  await expect(nextAction.getByRole("button", { name: "Abrir Configurar Gemini API key como proxima acao" })).toBeVisible();
  const readiness = page.getByLabel("Resumo de prontidao do onboarding");
  await expect(readiness).toContainText("3/4 checks OK");
  await expect(readiness).toContainText("Gemini: key ausente");
  await expect(readiness).toContainText("Modo: paper only");
  const mode3SetupState = page.getByLabel("Estado bloqueado do Mode 3");
  await expect(mode3SetupState).toContainText("Mode 3 autonomia real bloqueada");
  await expect(mode3SetupState).toContainText("org flags, human approval, hard daily limits, audit evidence, and production risk gates");
  await expect(mode3SetupState.getByRole("button", { name: "Abrir Bot Modes para revisar Mode 3 bloqueado" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Configurar Gemini: 2. Configurar Gemini API key" })).toBeVisible();
  const firstPromptCard = page.getByLabel("Primeira pergunta sugerida para o bot");
  await expect(firstPromptCard).toContainText(
    "Revise minha configuracao e sugira o primeiro paper trade seguro para testar hoje.",
  );
  await expect(page.locator("#first-prompt-copy-status")).toContainText("Copie e cole no Bot Modes");
  await firstPromptCard.getByRole("button", { name: "Copiar prompt" }).click();
  await expect(page.locator("#first-prompt-copy-status")).toContainText("Prompt copiado");
  await firstPromptCard.getByRole("button", { name: "Ir para Settings para completar o onboarding" }).click();
  await expect(page.getByText("Gemini bot credential")).toBeVisible();

  await page.getByRole("button", { name: "Overview" }).click();
  await expect(page.getByText("1 simulated trades recorded in this workspace")).toBeVisible();
  await expect(page.getByRole("cell", { name: "MOON" })).toBeVisible();
});

test("overview KPI cards show account zeros instead of demo fallbacks", async ({ page }) => {
  await page.unroute("**/api/v1/saas/dashboard");
  await page.route("**/api/v1/saas/dashboard", async (route) => {
    await route.fulfill({
      json: {
        email: account.email,
        org_id: account.org_id,
        plan: account.plan,
        requests_this_month: 0,
        simulated_volume_usd: 0,
        active_api_keys: 0,
        total_simulated_trades: 0,
        total_volume_usd: 0,
        total_fees_usd: 0,
        realized_pnl_usd: 0,
        unrealized_pnl_usd: 0,
        last_30_days_chart_points: [],
      },
    });
  });
  await page.unroute("**/api/v1/saas/usage");
  await page.route("**/api/v1/saas/usage", async (route) => {
    await route.fulfill({
      json: {
        requests_today: 0,
        requests_month: 0,
        plan: account.plan,
        quota: { simulate_order: 1000, token_audit: 100, active_api_keys: 3 },
      },
    });
  });

  await signIn(page, "Login");

  await page.getByRole("button", { name: "Overview" }).click();
  await expect(page.locator(".kpi", { hasText: "Tokens Analyzed" }).locator(".kpi-value")).toHaveText("0");
  await expect(page.locator(".kpi", { hasText: "Tokens Analyzed" })).toContainText("0 blocked by audit");
  await expect(page.locator(".kpi", { hasText: "Volume" }).locator(".kpi-value")).toHaveText("$0.00");
  await expect(page.locator(".kpi", { hasText: "Volume" })).toContainText("No simulated volume yet");
  await expect(page.getByText("$1,247.83")).toHaveCount(0);
  await expect(page.locator(".kpi", { hasText: "Audit Score" })).not.toContainText("82/100");
  await expect(page.getByText("0 simulated trades recorded in this workspace")).toBeVisible();
});

test("API keys page renders tenant-scoped empty state", async ({ page }) => {
  await page.unroute("**/api/v1/saas/api-keys");
  await page.route("**/api/v1/saas/api-keys", async (route) => {
    await route.fulfill({ json: [] });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "API Keys" }).click();

  await expect(page.getByLabel("API key summary")).toContainText("Total keys0");
  await expect(page.getByLabel("API key summary")).toContainText("Active0");
  await expect(page.getByLabel("API key summary")).toContainText("Last usednever");
  await expect(page.getByText("No API keys yet. Create a scoped key before running paper simulations from this browser.")).toBeVisible();
  await expect(page.getByText("ttm_sk_test...")).toHaveCount(0);
});

test("audit log renders tenant-scoped empty state", async ({ page }) => {
  await page.unroute("**/api/v1/saas/audit-log");
  await page.route("**/api/v1/saas/audit-log", async (route) => {
    await route.fulfill({ json: { org_id: 1, events: [], limit: 50, offset: 0 } });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "Audit Log" }).click();

  await expect(page.getByLabel("Audit log summary")).toContainText("Events loaded0");
  await expect(page.getByLabel("Audit log summary")).toContainText("Latest eventnone");
  await expect(page.getByText("No audit events yet for this workspace. Create a key, run a simulation, or approve a proposal to populate this tenant log.")).toBeVisible();
  await expect(page.getByText("api_key created")).toHaveCount(0);
});

test("overview renders empty copilot queue state", async ({ page }) => {
  await page.unroute("**/api/v1/copilot/proposals");
  await page.route("**/api/v1/copilot/proposals", async (route) => {
    await route.fulfill({ json: { org_id: 1, proposals: [] } });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "Overview" }).click();

  await expect(page.getByText("No copilot proposals yet. Run a paper simulation or wait for the scanner to queue a tenant-scoped recommendation.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve proposal" })).toHaveCount(0);
});

test("bot modes renders empty account-scoped approvals table", async ({ page }) => {
  await page.unroute("**/api/v1/copilot/proposals");
  await page.route("**/api/v1/copilot/proposals", async (route) => {
    await route.fulfill({ json: { org_id: account.org_id, proposals: [] } });
  });

  await signIn(page, "Login");
  await openWorkspaceNav(page, /Bot Modes/);

  const executionState = page.getByLabel("Paper testnet readiness and approval state");
  await expect(executionState).toContainText("Paper/testnet readiness and approval state");
  await expect(executionState).toContainText("Gemini key ausente");
  await expect(executionState).toContainText("no approvals queued");
  await expect(executionState).toContainText("Locked: no live funds, autonomous signing, or mainnet order submission from this runtime.");
  await expect(page.getByText("No pending approvals for this workspace yet. Run the scanner or a paper simulation to create account-scoped copilot proposals before approving execution.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "MOON" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Approve proposal" })).toHaveCount(0);
});

test("mode 2 approval queue shows rejected and cancelled feedback", async ({ page }) => {
  await page.unroute("**/api/v1/copilot/proposals");
  await page.route("**/api/v1/copilot/proposals", async (route) => {
    await route.fulfill({
      json: {
        org_id: account.org_id,
        proposals: [
          { ...proposal, id: 31, symbol: "REJECT", status: "rejected" },
          { ...proposal, id: 32, symbol: "CANCEL", status: "cancelled" },
        ],
      },
    });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "Overview" }).click();
  await expect(page.getByText("Rejected - no trade").first()).toBeVisible();
  await expect(page.getByText("Cancelled - no trade").first()).toBeVisible();
  await expect(page.getByText("Rejected by reviewer. Kept as Mode 2 no-execution evidence; no paper trade was created.")).toBeVisible();
  await expect(page.getByText("Cancelled before approval. Kept visible so it is not confused with executed paper trades.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve proposal" })).toHaveCount(0);

  await page.getByLabel("Workspace navigation").getByRole("button", { name: /Bot Modes/ }).click();
  await expect(page.getByText("Rejected - no trade").first()).toBeVisible();
  await expect(page.getByText("Cancelled - no trade").first()).toBeVisible();
  await expect(page.getByText("Rejected by reviewer. Kept as Mode 2 no-execution evidence; no paper trade was created.").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve proposal" })).toHaveCount(0);
});

test("bot modes preserve paper scanner, approval queue, and locked real autonomy safety", async ({ page }) => {
  const scanRequests: Array<{ execution_mode?: string }> = [];
  const fullGeminiKey = "AIzaSyFullSecretShouldNeverRender1234567890";
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/copilot/scan")) {
      scanRequests.push(JSON.parse(request.postData() || "{}") as { execution_mode?: string });
    }
  });
  await page.unroute("**/api/v1/saas/account");
  await page.route("**/api/v1/saas/account", async (route) => {
    await route.fulfill({
      json: {
        ...account,
        provider_credentials: {
          gemini: {
            configured: true,
            provider: "gemini",
            key_hint: "AIzaSy...7890",
            model: "gemini-3-flash-preview",
          },
        },
      },
    });
  });

  await signIn(page, "Login");
  await openWorkspaceNav(page, /Bot Modes/);

  await expect(page.getByLabel("Bot mode safety summary")).toContainText("paper-only");
  await expect(page.getByLabel("Bot mode safety summary")).toContainText("configured");
  await expect(page.getByText("AIzaSy...7890")).toBeVisible();
  await expect(page.getByText(fullGeminiKey)).toHaveCount(0);
  await expect(page.getByText("Modo 3 - Autonomo real bloqueado")).toBeVisible();
  await expect(page.getByText(/live order/i)).toHaveCount(0);

  const scannerReadiness = page.getByLabel("Prontidao do scanner");
  await expect(scannerReadiness).toContainText("Scanner pronto");
  await expect(scannerReadiness).toContainText("Mode 1 paper auto");

  await page.getByRole("button", { name: /Modo 3 - Autonomo real bloqueado/ }).click();
  await expect(page.getByRole("button", { name: /Pausar ciclo|Ativar ciclo automatico/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Enfileirar aprovacao real" })).toBeDisabled();

  await page.getByRole("button", { name: /Modo 1 - Paper\/Testnet automatico/ }).click();
  await page.getByRole("button", { name: "Rodar paper agora" }).click();
  await expect(page.getByText("1 propostas executadas em paper; 0 bloqueadas.")).toBeVisible();
  await page.getByRole("button", { name: /Modo 2 - Human-in-the-loop/ }).click();
  await expect(page.getByRole("button", { name: "Enfileirar aprovacao real" })).toBeDisabled();
  await expect(page.getByLabel("Bot mode safety summary")).toContainText("org locked");
  const scannerBlockers = page.getByLabel("Bloqueios do scanner");
  await expect(scannerBlockers).toContainText("MetaMask nao conectada");
  await expect(scannerBlockers).toContainText("real mode precisa de liberacao do org");

  expect(scanRequests).toEqual([
    expect.objectContaining({ execution_mode: "paper_auto" }),
  ]);
});

test("overview renders new-account dashboard empty states", async ({ page }) => {
  await page.unroute("**/api/v1/saas/dashboard");
  await page.route("**/api/v1/saas/dashboard", async (route) => {
    await route.fulfill({
      json: {
        email: account.email,
        org_id: account.org_id,
        plan: account.plan,
        requests_this_month: 0,
        simulated_volume_usd: 0,
        active_api_keys: 0,
        total_simulated_trades: 0,
        total_volume_usd: 0,
        total_fees_usd: 0,
        realized_pnl_usd: 0,
        unrealized_pnl_usd: 0,
        last_30_days_chart_points: [],
      },
    });
  });
  await page.unroute("**/api/v1/saas/usage");
  await page.route("**/api/v1/saas/usage", async (route) => {
    await route.fulfill({
      json: {
        requests_today: 0,
        requests_month: 0,
        plan: account.plan,
        quota: { simulate_order: 1000, token_audit: 100, active_api_keys: 3 },
      },
    });
  });
  await page.unroute("**/api/v1/saas/api-keys");
  await page.route("**/api/v1/saas/api-keys", async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.unroute("**/api/v1/copilot/proposals");
  await page.route("**/api/v1/copilot/proposals", async (route) => {
    await route.fulfill({ json: { org_id: 1, proposals: [] } });
  });
  await page.unroute("**/api/payments/orders");
  await page.route("**/api/payments/orders", async (route) => {
    await route.fulfill({ json: [] });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "Overview" }).click();

  await expect(page.getByText("No paper trading history yet")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run first simulation" })).toBeVisible();
  await expect(page.getByText("No agent activity yet")).toBeVisible();
  await expect(page.getByText("Create a scoped API key, connect Gemini, or run the scanner so token proposals and paper execution appear here.")).toBeVisible();
  await expect(page.getByLabel("Equity curve")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Notifications coming soon" })).toBeDisabled();
});

test("authenticated SaaS navigation renders dashboard sections", async ({ page }) => {
  await signIn(page, "Login");

  await expect(page.getByRole("heading", { name: "Guia de Setup" })).toBeVisible();
  await expect(page.getByLabel("Workspace navigation")).toBeVisible();
  await page.getByRole("button", { name: "Abrir docs" }).click();
  await expect(page.getByText("Primeira sessao recomendada")).toBeVisible();
  await page.getByLabel("Filter endpoint reference").fill("arc");
  await expect(page.getByText("1 of 7 endpoints")).toBeVisible();
  await expect(page.getByText("/api/arc/jobs")).toBeVisible();
  await expect(page.getByText("/health")).toHaveCount(0);
  await page.getByLabel("Filter endpoint reference").fill("not-a-real-endpoint");
  await expect(page.getByText("No endpoints match this filter. Try a path like /health or auth like jwt.")).toBeVisible();
  await page.getByLabel("Filter endpoint reference").fill("");
  await expect(page.getByText("7 of 7 endpoints")).toBeVisible();

  await page.getByRole("button", { name: "API Keys" }).click();
  await expect(page.getByRole("button", { name: "API Keys" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByLabel("API key summary")).toContainText("Total keys1");
  await expect(page.getByLabel("API key summary")).toContainText("Active1");
  await expect(page.getByLabel("API key summary")).toContainText("Revoked0");
  await expect(page.getByText("ttm_sk_test...")).toBeVisible();
  await expect(page.getByText("ACTIVE", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.locator('input[value="E2E Org"]')).toBeVisible();
  await expect(page.getByText("Quota this month")).toBeVisible();
  await expect(page.getByLabel("Billing invoice summary")).toContainText("1");
  await expect(page.getByLabel("Billing invoice summary")).toContainText("$49.00");
  await expect(page.getByLabel("Billing invoice summary")).toContainText("free");
  await expect(page.getByLabel("Billing invoice summary")).toContainText("988");
  await expect(page.getByLabel("Latest billing invoice")).toContainText("inv_smoke");
  await expect(page.getByLabel("Latest billing invoice")).toContainText("$49.00 billed to this workspace");
  await expect(page.getByRole("link", { name: "Open invoice" })).toHaveAttribute("href", "https://billing.example.test/invoices/inv_smoke");
  await expect(page.getByText("Gemini bot credential")).toBeVisible();

  await page.getByRole("button", { name: "Audit Log" }).click();
  await expect(page.getByLabel("Audit log summary")).toContainText("Events loaded1");
  await expect(page.getByLabel("Audit log summary")).toContainText("api_key created");
  await expect(page.getByRole("table").getByText("api_key created")).toBeVisible();

  await page.getByRole("button", { name: "Market Data" }).click();
  await expect(page.getByLabel("Provider health summary")).toContainText("Reported0");
  await expect(page.getByLabel("Provider health summary")).toContainText("No providers reported");
  await expect(page.getByRole("columnheader", { name: "Order submission" })).toBeVisible();
  await expect(page.getByText("No external provider health reported yet. Market data remains read-only and order submission stays blocked.")).toBeVisible();
});

test("market data summarizes degraded provider health without enabling orders", async ({ page }) => {
  await page.unroute("**/health");
  await page.route("**/health", async (route) => {
    await route.fulfill({
      json: {
        ok: true,
        app: "ToTheMoonTokens",
        mode: "paper",
        orderSubmissionEnabled: false,
        wallet_provider: "circle",
        wallet_set_id: "test-wallet-set",
        wallets_loaded: true,
        settlement_auth_mode: "manual",
        autonomous_payments_enabled: false,
        providers: {
          market: { status: "ok", latency_ms: 31, last_error: null },
          security: { status: "degraded", latency_ms: 53, last_error: "rate limit" },
        },
      },
    });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "Market Data" }).click();

  await expect(page.getByLabel("Provider health summary")).toContainText("Reported2");
  await expect(page.getByLabel("Provider health summary")).toContainText("Healthy1");
  await expect(page.getByLabel("Provider health summary")).toContainText("Latency avg42ms");
  await expect(page.getByLabel("Provider health summary")).toContainText("1 need attention");
  await expect(page.getByRole("row", { name: /security/i })).toContainText("rate limit");
  await expect(page.getByRole("row", { name: /security/i })).toContainText("BLOCKED");
  await expect(page.locator(".kpi", { hasText: "Order submission" })).toContainText("OFF");
});

test("paper trading scanner explains missing readiness before scan", async ({ page }) => {
  await page.unroute("**/api/v1/saas/dashboard");
  await page.route("**/api/v1/saas/dashboard", async (route) => {
    await route.fulfill({
      json: {
        email: account.email,
        org_id: account.org_id,
        plan: account.plan,
        requests_this_month: 0,
        simulated_volume_usd: 0,
        active_api_keys: 0,
        total_simulated_trades: 0,
        total_volume_usd: 0,
        total_fees_usd: 0,
        realized_pnl_usd: 0,
        unrealized_pnl_usd: 0,
        last_30_days_chart_points: [],
      },
    });
  });

  await signIn(page, "Login");
  await openWorkspaceNav(page, /Paper Trading/);

  await expect(page.locator("#paper-scanner-status")).toHaveText("Scanner pausado: Gemini key ausente, API key de simulacao ausente.");
  await expect(page.getByLabel("Paper trading execution mode state")).toContainText("Autonomous paper scanner");
  await expect(page.getByLabel("Paper trading execution mode state")).toContainText("Approval queue");
  await expect(page.getByLabel("Paper trading execution mode state")).toContainText("Real autonomy locked");
  await expect(page.getByRole("button", { name: "Scan + execute paper" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Queue approvals" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Submit single paper override" })).toBeEnabled();
});

test("bot modes shows zero scanner evidence without demo fallback", async ({ page }) => {
  await page.unroute("**/api/v1/saas/dashboard");
  await page.route("**/api/v1/saas/dashboard", async (route) => {
    await route.fulfill({
      json: {
        email: account.email,
        org_id: account.org_id,
        plan: account.plan,
        requests_this_month: 0,
        simulated_volume_usd: 0,
        active_api_keys: 0,
        total_simulated_trades: 0,
        total_volume_usd: 0,
        total_fees_usd: 0,
        realized_pnl_usd: 0,
        unrealized_pnl_usd: 0,
        last_30_days_chart_points: [],
      },
    });
  });
  await page.unroute("**/api/v1/copilot/proposals");
  await page.route("**/api/v1/copilot/proposals", async (route) => {
    await route.fulfill({ json: { org_id: account.org_id, proposals: [] } });
  });

  await signIn(page, "Login");
  await openWorkspaceNav(page, /Bot Modes/);

  await expect(page.getByLabel("Scanner run evidence")).toContainText("no run yet");
  await expect(page.getByText("No scanner run captured in this browser session")).toBeVisible();
  await expect(page.getByText("MOON")).toHaveCount(0);
  await expect(page.getByText("$1,247.83")).toHaveCount(0);
});

test("bot modes renders persisted Mode 1 scanner run evidence", async ({ page }) => {
  await page.unroute("**/api/v1/saas/account");
  await page.route("**/api/v1/saas/account", async (route) => {
    await route.fulfill({
      json: {
        ...account,
        provider_credentials: {
          gemini: {
            configured: true,
            provider: "gemini",
            key_hint: "AIzaSy...7890",
            model: "gemini-3-flash-preview",
          },
        },
      },
    });
  });
  await page.unroute("**/api/v1/copilot/scan");
  await page.route("**/api/v1/copilot/scan", async (route) => {
    const scannedProposal = {
      ...proposal,
      id: 31,
      token_address: "0xSAFEPAPER",
      symbol: "SAFE",
      status: "executed",
      approved_at: "2026-05-02T12:45:00.000Z",
      execution_payload: { source: "paper_simulation", trade_id: 9001 },
    };
    await route.fulfill({
      status: 201,
      json: {
        org_id: account.org_id,
        execution_mode: "paper_auto",
        scanner_run: {
          id: 4242,
          org_id: account.org_id,
          query: "SAFE",
          execution_mode: "paper_auto",
          status: "completed",
          thresholds: { min_volume: 100000 },
          candidate_count: 3,
          blocked_count: 2,
          queued_count: 0,
          paper_executed_count: 1,
          provider_readiness_summary: { ready: true },
          error_summary: null,
          created_at: "2026-05-02T12:44:59.000Z",
          completed_at: "2026-05-02T12:45:01.000Z",
        },
        created: [scannedProposal],
        skipped: [],
      },
    });
  });

  await signIn(page, "Login");
  await openWorkspaceNav(page, /Bot Modes/);
  await page.locator("label", { hasText: "Universo de busca" }).getByRole("textbox").fill("SAFE");
  await page.getByRole("button", { name: "Rodar paper agora" }).click();

  const evidence = page.getByLabel("Scanner run evidence");
  await expect(evidence).toContainText("Last run: Mode 1 paper auto for SAFE");
  await expect(evidence).toContainText("Run ID4242");
  await expect(evidence).toContainText("Statuscompleted");
  await expect(evidence).toContainText("Candidates3");
  await expect(evidence).toContainText("Paper executed1");
  await expect(evidence).toContainText("Blocked2");
  await expect(evidence).toContainText("SAFE");
  await expect(evidence).toContainText("paper executed");
  await expect(page.getByLabel("Scanner next action")).toContainText("Review paper ledger and keep Mode 1 running");
  await expect(page.getByLabel("Scanner next action")).toContainText("no human approval or real order is required");
  await expect(evidence).not.toContainText("No scanner run captured in this browser session");
  await expect(evidence).not.toContainText("MOON");
});

test("mobile navigation opens, closes, and updates active section", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "Login");

  const navigation = page.getByLabel("Workspace navigation");
  await expect(navigation).not.toBeInViewport();

  const menuButton = page.getByRole("button", { name: "Open navigation" });
  await expect(menuButton).toHaveAttribute("aria-controls", "workspace-navigation");
  await expect(menuButton).toHaveAttribute("aria-expanded", "false");

  await menuButton.click();
  await expect(navigation).toBeInViewport();
  await expect(page.getByRole("button", { name: "Close navigation", exact: true })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Close navigation panel" })).toBeFocused();
  await expect(page.getByRole("button", { name: "Close navigation overlay" })).toBeVisible();
  await page.keyboard.press("Shift+Tab");
  await expect(navigation.getByRole("button", { name: "Settings" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Close navigation panel" })).toBeFocused();

  await page.getByRole("button", { name: "API Keys" }).click();
  await expect(page).toHaveURL(/\/app\/api-keys$/);
  await expect(navigation).not.toBeInViewport();
  await expect(menuButton).toBeFocused();
  await expect(page.getByRole("button", { name: "API Keys" })).toHaveAttribute("aria-current", "page");

  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(navigation).toBeInViewport();
  await expect(navigation.getByRole("button", { name: "API Keys" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(navigation).not.toBeInViewport();
  await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
});

test("settings quota reflects the authenticated account active key count", async ({ page }) => {
  await page.unroute("**/api/v1/saas/dashboard");
  await page.route("**/api/v1/saas/dashboard", async (route) => {
    await route.fulfill({
      json: {
        email: account.email,
        org_id: account.org_id,
        plan: account.plan,
        requests_this_month: 12,
        simulated_volume_usd: 100,
        active_api_keys: 2,
        total_simulated_trades: 1,
        total_volume_usd: 100,
        total_fees_usd: 8,
        realized_pnl_usd: 0,
        unrealized_pnl_usd: 0,
        last_30_days_chart_points: [],
      },
    });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "Settings", exact: true }).click();

  await expect(page.getByText("Active keys")).toBeVisible();
  await expect(page.getByText("2 / 3")).toBeVisible();
});

test("recoverable data refresh errors can be retried", async ({ page }) => {
  let usageAttempts = 0;
  let releaseHealthRefresh!: () => void;
  const delayedHealthRefresh = new Promise<void>((resolve) => {
    releaseHealthRefresh = resolve;
  });
  let delayNextHealthRefresh = false;
  let healthAttempts = 0;
  await page.unroute("**/health");
  await page.route("**/health", async (route) => {
    healthAttempts += 1;
    if (delayNextHealthRefresh && healthAttempts > 2) await delayedHealthRefresh;
    await route.fulfill({ json: defaultHealth });
  });
  await page.unroute("**/api/v1/saas/usage");
  await page.route("**/api/v1/saas/usage", async (route) => {
    usageAttempts += 1;
    if (usageAttempts === 1) {
      await route.fulfill({ status: 503, json: { detail: "Usage service unavailable" } });
      return;
    }
    await route.fulfill({
      json: {
        requests_today: 3,
        requests_month: 12,
        plan: account.plan,
        quota: { simulate_order: 1000, token_audit: 100, active_api_keys: 3 },
      },
    });
  });

  await signIn(page, "Login");

  await expect(page.getByRole("alert")).toContainText("Usage service unavailable");
  await page.getByRole("button", { name: "Overview" }).click();
  await expect(page.getByRole("cell", { name: "MOON" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("alert")).toBeHidden();
  await expect(page.getByText("3 today").first()).toBeVisible();

  delayNextHealthRefresh = true;
  await page.getByRole("button", { name: "Refresh data" }).click();
  await expect(page.getByRole("button", { name: "Refreshing data" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Refreshing data" })).toHaveAttribute("aria-busy", "true");
  releaseHealthRefresh();
  await expect(page.getByRole("button", { name: "Refresh data" })).toBeEnabled();
});

test("API key, simulation, proposal, and billing actions call expected endpoints", async ({ page }) => {
  const requests: Array<{ method: string; url: string; body: string | null }> = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/") || request.url().includes("/api/agent/")) {
      requests.push({ method: request.method(), url: request.url(), body: request.postData() });
    }
  });

  await signIn(page);

  await page.getByRole("button", { name: "API Keys" }).click();
  await page.getByPlaceholder("ttm_sk_live_...").fill("ttm_sk_live_manual");

  await page.locator("label", { hasText: "Name" }).getByRole("textbox").fill("qa-key");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByPlaceholder("ttm_sk_live_...")).toHaveValue("ttm_sk_live_created");
  await page.getByRole("button", { name: "Revoke key" }).click();
  await expect(page.getByText("API key revoked.")).toBeVisible();

  await page.getByRole("button", { name: "Overview" }).click();
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByText("Simulation recorded.")).toBeVisible();

  await openWorkspaceNav(page, /Bot Modes/);
  await expect(page.getByLabel("Bot mode safety summary")).toContainText("paper-only");
  await expect(page.getByLabel("Bot mode safety summary")).toContainText("locked");
  await expect(page.getByLabel("Bot mode safety summary")).toContainText("1");

  await page.getByRole("button", { name: "Approve proposal" }).click();
  await expect(page.getByText("Proposal executed.")).toBeVisible();

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.locator("label", { hasText: "Gemini API key" }).getByRole("textbox").fill("AIzaSy...7890");
  await expect(page.locator("#gemini-key-secret-hint")).toContainText("never echoes the full secret");
  await expect(page.locator("label", { hasText: "Gemini API key" }).getByRole("textbox")).toHaveAttribute("aria-describedby", "gemini-key-secret-hint");
  await expect(page.getByText("This looks like a masked key hint")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save Gemini key disabled because the value is masked" })).toBeDisabled();

  await page.locator("label", { hasText: "Gemini API key" }).getByRole("textbox").fill("AIzaSyTestGeminiKey1234567890");
  await page.getByRole("button", { name: "Save Gemini key" }).click();
  await expect(page.getByText("Gemini API key salva na sua conta.")).toBeVisible();
  await expect(page.getByText("Configured as AIzaSy...7890")).toBeVisible();
  await expect(page.getByLabel("Gemini credential summary")).toContainText("configured");
  await expect(page.getByLabel("Gemini credential summary")).toContainText("AIzaSy...7890");
  await expect(page.getByLabel("Gemini credential summary")).toContainText("gemini-3-flash-preview");
  await expect(page.getByText("AIzaSyTestGeminiKey1234567890")).toHaveCount(0);

  await openWorkspaceNav(page, /Bot Modes/);
  await expect(page.getByText("Agent console")).toBeVisible();
  await page.getByRole("button", { name: "Enviar mensagem" }).click();
  await expect(page.getByText("Recebi: Explique o ultimo ciclo do scanner e o proximo paper test seguro.")).toBeVisible();
  await page.getByRole("button", { name: "Conectar wallet" }).click();
  await expect(page.getByText("0x742d35Cc...38f44e")).toBeVisible();
  await expect(page.getByText("0x2105")).toBeVisible();

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Gemini credential summary")).toContainText("configured");
  await expect(page.getByRole("button", { name: "Remove key" })).toBeEnabled();
  await page.getByRole("button", { name: "Remove key" }).click();
  await expect(page.getByLabel("Gemini credential summary")).toContainText("missing");
  await expect(page.getByRole("button", { name: "Remove key" })).toBeDisabled();

  await page.getByRole("button", { name: "Select Pro billing plan" }).click();
  await expect(page.getByText("Checkout opened.")).toBeVisible();

  expect(requests).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/v1/simulate/order") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/v1/copilot/proposals/11/approve") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/v1/saas/api-keys") }),
      expect.objectContaining({ method: "DELETE", url: expect.stringContaining("/api/v1/saas/api-keys/7") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/v1/saas/provider-credentials/gemini") }),
      expect.objectContaining({ method: "DELETE", url: expect.stringContaining("/api/v1/saas/provider-credentials/gemini") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/agent/chat") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/v1/billing/checkout") }),
    ]),
  );
});

test("Mode 2 uses MetaMask approval evidence without submitting autonomous orders", async ({ page }) => {
  const requests: Array<{ method: string; url: string; body: string | null }> = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/copilot/")) {
      requests.push({ method: request.method(), url: request.url(), body: request.postData() });
    }
  });

  await page.unroute("**/api/v1/saas/account");
  await page.route("**/api/v1/saas/account", async (route) => {
    await route.fulfill({
      json: {
        ...account,
        real_mode_enabled: true,
        real_mode_approved_at: "2026-05-02T12:00:00.000Z",
        provider_credentials: {
          gemini: {
            configured: true,
            provider: "gemini",
            key_hint: "AIzaSy...7890",
            model: "gemini-3-flash-preview",
          },
        },
      },
    });
  });

  await signIn(page, "Login");
  await openWorkspaceNav(page, /Bot Modes/);
  await page.getByRole("button", { name: "Modo 2 - Human-in-the-loop" }).click();
  await expect(page.getByLabel("Bot mode safety summary")).toContainText("manual ate $50.00/dia");
  await expect(page.getByLabel("Paper testnet readiness and approval state")).toContainText("1 pending human approval");
  await expect(page.getByRole("button", { name: "Enfileirar aprovacao real" })).toBeDisabled();

  await page.getByRole("button", { name: "Conectar wallet" }).click();
  await expect(page.getByText("0x742d35Cc...38f44e")).toBeVisible();
  await page.getByRole("button", { name: "Enfileirar aprovacao real" }).click();
  await expect(page.getByText("1 propostas criadas para aprovacao real; 0 bloqueadas.")).toBeVisible();
  await expect(page.getByText("Mode 2 real approval", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Paper testnet readiness and approval state")).toContainText("1 pending human approval");

  await page.getByRole("button", { name: "Sign manual approval" }).click();
  await expect(page.getByText("Aprovacao manual registrada; ordem real continua fora do runtime.")).toBeVisible();
  expect(requests).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        method: "POST",
        url: expect.stringContaining("/api/v1/copilot/scan"),
        body: expect.stringContaining('"execution_mode":"real_approval"'),
      }),
      expect.objectContaining({
        method: "POST",
        url: expect.stringContaining("/api/v1/copilot/proposals/12/approve"),
        body: expect.stringContaining('"wallet_signature":"0xsigned"'),
      }),
    ]),
  );
});

test("settings billing actions announce pending checkout and guard account actions", async ({ page }) => {
  let markCheckoutRequested!: () => void;
  const checkoutRequested = new Promise<void>((resolve) => {
    markCheckoutRequested = resolve;
  });
  let finishCheckout!: () => void;
  const checkoutFulfilled = new Promise<void>((resolve) => {
    finishCheckout = resolve;
  });

  await page.unroute("**/api/v1/billing/checkout");
  await page.route("**/api/v1/billing/checkout", async (route) => {
    markCheckoutRequested();
    await checkoutFulfilled;
    await route.fulfill({ json: { url: "https://billing.example.test/session", org_id: 1, plan: "enterprise" } });
  });

  await signIn(page, "Login");
  await page.getByRole("button", { name: "Settings", exact: true }).click();

  const proButton = page.getByRole("button", { name: "Select Pro billing plan" });
  const enterpriseButton = page.getByRole("button", { name: "Select Enterprise billing plan" });
  const signOutButton = page.getByRole("button", { name: "Sign out" }).last();

  await enterpriseButton.click();
  await checkoutRequested;

  await expect(page.getByRole("button", { name: "Opening Enterprise checkout" })).toBeDisabled();
  await expect(proButton).toBeDisabled();
  await expect(page.getByRole("button", { name: "Sign out disabled while billing or data refresh is pending" })).toBeDisabled();
  await expect(page.getByRole("status").filter({ hasText: "Opening Enterprise checkout. Account actions are temporarily disabled." })).toBeVisible();

  finishCheckout();

  await expect(page.getByText("Checkout opened.")).toBeVisible();
  await expect(enterpriseButton).toBeEnabled();
  await expect(signOutButton).toBeEnabled();
});

test("paid agent lifecycle shows payment, Nexus, Arc, and delivery gates", async ({ page }) => {
  const requests: Array<{ method: string; url: string }> = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) {
      requests.push({ method: request.method(), url: request.url() });
    }
  });

  await openAuthenticatedRoute(page, "/app/nanopayments");
  await expect(page.getByRole("heading", { name: "Paid Agent Lifecycle" })).toBeVisible();
  await expect(page.getByRole("radio", { name: /Delivery Packet/ })).toBeVisible();
  await expect(page.getByText("No mainnet execution")).toBeVisible();
  await expect(page.getByText("Payment requirement is open. Verify the test transaction before delivery unlocks.")).toBeVisible();
  await expect(page.getByText("stubbed")).toBeVisible();
  await expect(page.getByLabel("Payment safety summary")).toContainText("pending");
  await expect(page.getByLabel("Payment safety summary")).toContainText("locked");
  await expect(page.getByLabel("Lifecycle gates").getByText("pending")).toBeVisible();
  await expect(page.getByLabel("Lifecycle gates").getByText("locked")).toBeVisible();

  await page.getByRole("button", { name: "Create payment requirement" }).click();
  await expect(page.getByText("Payment requirement created.")).toBeVisible();
  await expect(page.getByText("Payment requirement is open. Verify the test transaction before delivery unlocks.")).toBeVisible();
  await expect(page.getByText("pay_demo_1...567890")).toBeVisible();

  await page.getByRole("button", { name: "Verify payment" }).click();
  await expect(page.getByText("Payment verified.")).toBeVisible();
  await expect(page.getByText("Payment is verified. Unlock delivery to complete the paid work unit.")).toBeVisible();
  await expect(page.getByLabel("Lifecycle gates").getByText("PAYMENT_UNLOCKED")).toBeVisible();

  await page.getByRole("button", { name: "Unlock delivery" }).click();
  await expect(page.getByText("Delivery unlocked.")).toBeVisible();
  await expect(page.getByLabel("Payment safety summary")).toContainText("unlocked");
  await expect(page.getByLabel("Lifecycle gates").getByText("DELIVERED")).toBeVisible();
  await expect(page.getByLabel("Lifecycle gates").getByText("unlocked")).toBeVisible();

  expect(requests).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ method: "GET", url: expect.stringContaining("/api/payments/catalog") }),
      expect.objectContaining({ method: "GET", url: expect.stringContaining("/api/arc/jobs?limit=5") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/payments/intent") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/payments/verify") }),
      expect.objectContaining({ method: "POST", url: expect.stringContaining("/api/payments/execute") }),
      expect.objectContaining({ method: "GET", url: expect.stringContaining("/api/jobs/nexus-pay-demo") }),
    ]),
  );
});

test("new payment keeps its Nexus job when older orders are returned first", async ({ page }) => {
  const olderPayment = {
    ...pendingPayment,
    payment_id: "pay_old_1234567890",
    job_id: "nexus-pay-old",
    deposit_address: "0xOlderDepositAddress",
    created_at: "2026-05-01T12:00:00.000Z",
    updated_at: "2026-05-01T12:00:00.000Z",
  };
  let orders = [olderPayment];
  const requestedJobs: string[] = [];

  await page.route("**/api/payments/orders", async (route) => {
    await route.fulfill({ json: orders });
  });
  await page.route("**/api/payments/intent", async (route) => {
    orders = [olderPayment, pendingPayment];
    await route.fulfill({
      json: {
        payment_id: pendingPayment.payment_id,
        amount_usd: pendingPayment.amount_usd,
        currency: "USDC",
        deposit_address: pendingPayment.deposit_address,
        payment_requirement: { asset: "USDC", network: "arc_testnet", amount: "0.001" },
        status: pendingPayment.status,
        job_id: pendingPayment.job_id,
      },
    });
  });
  await page.route("**/api/jobs/nexus-pay-old", async (route) => {
    requestedJobs.push("nexus-pay-old");
    await route.fulfill({ json: { ...nexusJob, id: "nexus-pay-old", payment_id: olderPayment.payment_id } });
  });
  await page.route("**/api/jobs/nexus-pay-demo", async (route) => {
    requestedJobs.push("nexus-pay-demo");
    await route.fulfill({ json: nexusJob });
  });

  await openAuthenticatedRoute(page, "/app/nanopayments");
  await expect(page.getByText("pay_old_12...567890")).toBeVisible();

  await page.getByRole("button", { name: "Create payment requirement" }).click();

  await expect(page.getByText("Payment requirement created.")).toBeVisible();
  await expect(page.getByText("pay_demo_1...567890")).toBeVisible();
  expect(requestedJobs.at(-1)).toBe("nexus-pay-demo");
});

test("paid agent lifecycle hides unrelated Arc proofs", async ({ page }) => {
  await page.route("**/api/arc/jobs?limit=5", async (route) => {
    await route.fulfill({ json: [{ ...arcProof, payment_id: "pay_other_tenant", job_id: "job_other_tenant" }] });
  });

  await openAuthenticatedRoute(page, "/app/nanopayments");
  await expect(page.getByRole("heading", { name: "Paid Agent Lifecycle" })).toBeVisible();
  await expect(page.getByText("0xabcdef12")).toHaveCount(0);
  await expect(page.getByText("stubbed")).toHaveCount(0);
  await expect(page.getByText("not issued")).toBeVisible();
  await expect(page.getByText("stub unavailable")).toBeVisible();
});

test("paid agent lifecycle keeps delivery locked before payment verification", async ({ page }) => {
  const executeRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/payments/execute")) {
      executeRequests.push(request.url());
    }
  });

  await openAuthenticatedRoute(page, "/app/nanopayments");
  await expect(page.getByRole("heading", { name: "Paid Agent Lifecycle" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Unlock delivery" })).toBeDisabled();
  await expect(page.getByLabel("Lifecycle gates").getByText("locked")).toBeVisible();

  await page.getByRole("button", { name: "Create payment requirement" }).click();
  await expect(page.getByText("Payment requirement created.")).toBeVisible();
  await expect(page.getByLabel("Lifecycle gates").getByText("pending")).toBeVisible();
  await expect(page.getByRole("button", { name: "Unlock delivery" })).toBeDisabled();
  await expect(page.getByLabel("Lifecycle gates").getByText("locked")).toBeVisible();
  expect(executeRequests).toEqual([]);
});

test("paid agent lifecycle explains empty artifact catalog", async ({ page }) => {
  await page.unroute("**/api/payments/catalog");
  await page.route("**/api/payments/catalog", async (route) => {
    await route.fulfill({ json: [] });
  });

  await openAuthenticatedRoute(page, "/app/nanopayments");

  await expect(page.getByText("No priced artifacts are available for this workspace yet. Refresh after the catalog sync completes before creating a payment requirement.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create payment requirement" })).toBeDisabled();
  await expect(page.getByRole("radio")).toHaveCount(0);
});

test("agent chat clears stale payment state when backend returns no orders", async ({ page }) => {
  await signIn(page, "Login");

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.locator("label", { hasText: "Gemini API key" }).getByRole("textbox").fill("AIzaSyTestGeminiKey1234567890");
  await page.getByRole("button", { name: "Save Gemini key" }).click();
  await expect(page.getByText("Gemini API key salva na sua conta.")).toBeVisible();

  await page.evaluate(() => {
    window.history.pushState({}, "", "/app/nanopayments");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByRole("heading", { name: "Paid Agent Lifecycle" })).toBeVisible();
  await expect(page.getByLabel("Lifecycle gates").getByText("pending")).toBeVisible();

  await page.unroute("**/api/agent/chat");
  await page.route("**/api/agent/chat", async (route) => {
    await route.fulfill({
      json: {
        reply: "No active payment requirements remain for this account.",
        events: [],
        orders: [],
        settlement_auth_mode: "manual",
        autonomous_payments_enabled: false,
      },
    });
  });

  await openWorkspaceNav(page, /Bot Modes/);
  await page.getByRole("button", { name: "Enviar mensagem" }).click();
  await expect(page.getByText("No active payment requirements remain for this account.")).toBeVisible();

  await page.evaluate(() => {
    window.history.pushState({}, "", "/app/nanopayments");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByLabel("Lifecycle gates").getByText("required")).toBeVisible();
  await expect(page.getByLabel("Lifecycle gates").getByText("pending")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Verify payment" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Unlock delivery" })).toBeDisabled();

  await page.getByRole("button", { name: "Create payment requirement" }).click();
  await expect(page.getByText("Payment requirement created.")).toBeVisible();
  await expect(page.getByLabel("Lifecycle gates").getByText("pending")).toBeVisible();
  await expect(page.getByText("pay_demo_1...567890")).toBeVisible();
});
