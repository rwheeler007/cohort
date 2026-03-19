import { test, expect } from "@playwright/test";
import {
  typeInContentEditable,
  snap,
  DemoTimer,
  waitForSetupStep,
  waitForAgentResponse,
  waitForConnection,
} from "../helpers/demo-utils";

/**
 * Docker Demo: "Zero to Conversation"
 *
 * Assumes a Cohort container is already running (started by the
 * orchestrator script or manually). This spec just drives the
 * browser portion — the terminal `docker run` command is captured
 * separately by the CLI demo helper.
 *
 * Usage:
 *   # Start container first:
 *   docker run --rm -p 5100:5100 -v ./demo-data:/home/cohort/data cohort:latest serve
 *
 *   # Then run this spec:
 *   COHORT_URL=http://localhost:5100 npx playwright test specs/docker-demo.spec.ts
 */

const timer = new DemoTimer();

test.describe("Docker - Zero to Conversation", () => {
  test("container dashboard to first message", async ({ page }) => {
    timer.start();

    const baseUrl = process.env.COHORT_URL || "http://127.0.0.1:5100";
    await page.goto(baseUrl);
    await waitForConnection(page);
    timer.mark("page_loaded");
    await snap(page, "docker-loaded", 1);

    // If wizard appears, walk through it quickly
    const wizard = page.locator("#setup-wizard");
    if (await wizard.isVisible({ timeout: 3_000 }).catch(() => false)) {
      // Trigger step 1 (show() doesn't auto-run it)
      await page.evaluate(() => {
        if (typeof setupWizard !== "undefined") setupWizard.runStep1();
      });

      // Speed-run the wizard — just click through
      for (const step of [1, 2, 3, 4]) {
        await waitForSetupStep(page, step);
        await page.waitForTimeout(500);

        // Step 4: run verify if button exists
        if (step === 4) {
          const verifyBtn = page.locator("#setup-verify-btn");
          if (await verifyBtn.isVisible()) {
            await verifyBtn.click();
            await page.waitForSelector("#setup-verify-result", {
              state: "visible",
              timeout: 60_000,
            });
          }
        }

        await page.click("#setup-next-btn");
        await page.waitForTimeout(300);
      }

      // Skip optional steps, finish
      for (const step of [5, 6, 7]) {
        await waitForSetupStep(page, step);
        await page.waitForTimeout(200);
        if (step === 7) {
          const finishBtn = page.locator("#setup-finish-btn");
          if (await finishBtn.isVisible()) {
            await finishBtn.click();
          } else {
            await page.click("#setup-skip-btn");
          }
        } else {
          await page.click("#setup-skip-btn");
        }
        await page.waitForTimeout(200);
      }

      await page.waitForSelector("#setup-wizard", {
        state: "hidden",
        timeout: 5_000,
      });
      timer.mark("wizard_complete");
    }

    await page.waitForTimeout(500);
    await snap(page, "docker-dashboard", 2);

    // ---------------------------------------------------------------
    // Create channel
    // ---------------------------------------------------------------
    await page.click("#add-channel-btn");
    await page.waitForSelector("#create-channel-modal", {
      state: "visible",
    });

    await page.fill("#new-channel-name", "demo-first-chat");
    await page.click('#create-channel-form button[type="submit"]');
    await page.waitForTimeout(800);
    timer.mark("channel_created");

    // Navigate to channel
    const channelLink = page.locator(
      '#channel-list li:has-text("demo-first-chat")'
    );
    if (await channelLink.isVisible()) {
      await channelLink.click();
      await page.waitForTimeout(400);
    }

    await page.waitForSelector("#panel-chat", {
      state: "visible",
      timeout: 5_000,
    });
    await snap(page, "docker-channel", 3);

    // ---------------------------------------------------------------
    // First message (faster typing for Docker demo — it's the short one)
    // ---------------------------------------------------------------
    // Short "hello world" prompt — @mention triggers agent routing for a response
    await typeInContentEditable(
      page,
      "#message-input",
      "@python_developer Hello! Can you introduce yourself in one sentence?",
      { delay: 40, jitter: 20 }
    );

    await page.click("#send-btn");
    timer.mark("message_sent");

    await waitForAgentResponse(page, 90_000);
    await snap(page, "docker-response", 4);
    timer.mark("response_received");

    timer.mark("demo_complete");

    console.log("\n========================================");
    console.log("  DOCKER DEMO TIMING");
    console.log("========================================");
    console.log(timer.summary());
    console.log("========================================\n");

    timer.save("recordings/docker-timing.json");
  });
});
