const { defineConfig } = require("@playwright/test");

const port = Number(process.env.PLUSONE_E2E_PORT || 8765);
const baseURL = `http://127.0.0.1:${port}`;
const serverEnv = { ...process.env };
delete serverEnv.DATABASE_URL;
Object.assign(serverEnv, {
  PYTHON: process.env.PYTHON || "python",
  PLUSONE_E2E_PORT: String(port),
  DJANGO_DEBUG: "true",
  PLUSONE_MODERATION_MODE: "rules",
  PLUSONE_NEW_MATCHES_ENABLED: "true",
  SECURE_SSL_REDIRECT: "false",
  DEEPSEEK_API_KEY: "",
  OPENAI_API_KEY: "",
});
if (process.env.PLUSONE_E2E_DATABASE_URL) {
  serverEnv.PLUSONE_E2E_DATABASE_URL = process.env.PLUSONE_E2E_DATABASE_URL;
}

module.exports = defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 12_000 },
  use: {
    baseURL,
    browserName: "chromium",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "bash tests/browser/start_server.sh",
    url: `${baseURL}/healthz/`,
    timeout: 60_000,
    reuseExistingServer: false,
    env: serverEnv,
  },
});
