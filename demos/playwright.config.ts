import { defineConfig, devices } from "@playwright/test";

/**
 * Cohort "Zero to Conversation" demo recorder.
 *
 * Each spec walks through an install method, records video,
 * and captures timestamped screenshots at key moments.
 *
 * Usage:
 *   npx playwright test                       # all demos
 *   npx playwright test specs/web-ui-demo     # single demo
 *   COHORT_URL=http://host:5100 npx ...       # custom server
 */
export default defineConfig({
  testDir: "./specs",
  outputDir: "./recordings/artifacts",
  timeout: 120_000,
  expect: { timeout: 30_000 },

  /* Run specs serially — each one records a video */
  fullyParallel: false,
  workers: 1,

  use: {
    baseURL: process.env.COHORT_URL || "http://127.0.0.1:5100",
    /* Slow enough to look human on camera */
    launchOptions: { slowMo: 80 },
    /* Record everything */
    video: { mode: "on", size: { width: 1920, height: 1080 } },
    screenshot: "on",
    trace: "retain-on-failure",
    /* Viewport */
    viewport: { width: 1920, height: 1080 },
  },

  projects: [
    {
      name: "web-ui",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1920, height: 1080 },
        video: { mode: "on", size: { width: 1920, height: 1080 } },
      },
      testMatch: "web-ui-demo.spec.ts",
    },
    {
      name: "vscode-style",
      use: {
        ...devices["Desktop Chrome"],
        /* Narrower viewport simulates VS Code webview panel */
        viewport: { width: 1280, height: 800 },
        video: { mode: "on", size: { width: 1280, height: 800 } },
      },
      testMatch: "vscode-demo.spec.ts",
    },
    {
      name: "docker",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1920, height: 1080 },
        video: { mode: "on", size: { width: 1920, height: 1080 } },
      },
      testMatch: "docker-demo.spec.ts",
    },
  ],

  /* Web server — start Cohort if not already running */
  webServer: {
    command: "python -m cohort serve --port 5100",
    port: 5100,
    cwd: "g:/cohort",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
