import { defineConfig } from "@playwright/test";

const API_PORT = 8010;
const WEB_PORT = 3100;
const root = "../..";

export default defineConfig({
  testDir: "tests/e2e",
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 30_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: "uv run --locked python -m tests.support.e2e_backend",
      cwd: root,
      url: `http://127.0.0.1:${API_PORT}/health`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        E2E_API_PORT: String(API_PORT),
        TEST_DATABASE_URL:
          process.env.TEST_DATABASE_URL ?? "postgresql://gigbid:gigbid@localhost:5432/gigbid",
        TEST_REDIS_URL: process.env.TEST_REDIS_URL ?? "redis://localhost:6379/14",
      },
    },
    {
      command: `npm run build && npx next start -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/login`,
      timeout: 240_000,
      reuseExistingServer: !process.env.CI,
      env: { API_URL: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
