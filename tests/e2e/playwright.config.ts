import { defineConfig, devices } from "@playwright/test";
import * as path from "path";

/**
 * Cohort E2E Test Configuration
 *
 * Key differences from demos/playwright.config.ts:
 *   - No slowMo (speed over aesthetics)
 *   - No video recording by default
 *   - Isolated data directory (NEVER touches production data)
 *   - Parallel execution where possible
 *   - Shorter timeouts (tests should be fast)
 *
 * Usage:
 *   npx playwright test --config tests/e2e/playwright.config.ts
 *   npx playwright test --config tests/e2e/playwright.config.ts --grep @smoke
 *   npx playwright test --config tests/e2e/playwright.config.ts --grep "@chat|@settings"
 */

const E2E_PORT = parseInt(process.env.COHORT_E2E_PORT || "5199", 10);
const E2E_DATA_DIR = process.env.COHORT_E2E_DATA_DIR || path.join(
  require("os").tmpdir(),
  `cohort-e2e-${process.pid}`
);

export default defineConfig({
  testDir: "./specs",
  outputDir: "./test-results",
  timeout: 60_000,
  expect: { timeout: 15_000 },

  /* Allow parallel test execution for speed */
  fullyParallel: true,
  workers: 2,

  /* Only keep artifacts on failure */
  preserveOutput: "failures-only",

  /* Reporter: concise for CI, verbose locally */
  reporter: process.env.CI
    ? [["github"], ["json", { outputFile: "test-results/results.json" }]]
    : [["list"]],

  use: {
    baseURL: `http://127.0.0.1:${E2E_PORT}`,
    /* No slowMo -- tests should be fast */
    launchOptions: { slowMo: 0 },
    /* Screenshots only on failure */
    screenshot: "only-on-failure",
    /* No video by default (enable with --video on) */
    video: "off",
    /* Trace on first retry only */
    trace: "on-first-retry",
    /* Standard viewport */
    viewport: { width: 1280, height: 720 },
  },

  retries: process.env.CI ? 1 : 0,

  projects: [
    {
      name: "e2e",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
  ],

  /* Start an isolated Cohort server for testing.
   *
   * CRITICAL: Uses --data-dir with a temp directory so production
   * data is NEVER touched. See CLAUDE.md rule:
   * "Never Modify Production Data for Testing or Display"
   */
  webServer: {
    command: `python -m cohort serve --port ${E2E_PORT} --data-dir "${E2E_DATA_DIR}"`,
    port: E2E_PORT,
    cwd: path.resolve(__dirname, "../.."),
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: {
      COHORT_DATA_DIR: E2E_DATA_DIR,
    },
  },
});
