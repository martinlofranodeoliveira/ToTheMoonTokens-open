import { defineConfig, devices } from "@playwright/test";

const configuredE2EPort = process.env.TTM_E2E_PORT;
const e2ePort = configuredE2EPort && /^\d+$/.test(configuredE2EPort) ? configuredE2EPort : "4174";
const e2eBaseURL = process.env.TTM_E2E_BASE_URL ?? `http://127.0.0.1:${e2ePort}/`;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  webServer: process.env.TTM_E2E_BASE_URL
    ? undefined
    : {
        command: `npm run dev -- --host 127.0.0.1 --port ${e2ePort}`,
        url: e2eBaseURL,
        reuseExistingServer: !process.env.CI,
      },
  use: {
    baseURL: e2eBaseURL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
