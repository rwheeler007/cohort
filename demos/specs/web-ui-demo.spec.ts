import { test, expect } from "@playwright/test";
import {
  resetToFirstRun,
  restoreFromBackup,
  typeHuman,
  typeInContentEditable,
  snap,
  DemoTimer,
  waitForSetupStep,
  waitForAgentResponse,
  waitForConnection,
} from "../helpers/demo-utils";

/**
 * Web UI Demo: "Zero to Conversation"
 *
 * Records the full flow:
 *   1. Fresh page load -> setup wizard appears
 *   2. Walk through wizard steps (hardware, ollama, model, verify)
 *   3. Finish wizard
 *   4. Create a channel
 *   5. Send first message + get agent response
 *
 * Prerequisites:
 *   - Ollama running with model already downloaded
 *   - Cohort server running (or let playwright.config start it)
 *
 * Output:
 *   - recordings/web-ui-demo.webm  (full video)
 *   - recordings/XX-*.png          (step screenshots)
 *   - recordings/web-ui-timing.json (timing data)
 */

const timer = new DemoTimer();

test.describe("Web UI - Zero to Conversation", () => {
  test.beforeAll(async () => {
    await resetToFirstRun();
  });

  test.afterAll(async () => {
    await restoreFromBackup();
  });

  test("full setup and first message", async ({ page }) => {
    timer.start();

    // ---------------------------------------------------------------
    // Step 1: Load page — wizard should auto-appear
    // ---------------------------------------------------------------
    await page.goto("/");
    await waitForConnection(page);
    timer.mark("page_loaded");

    // Wait for setup wizard to appear (triggered by setup_completed === false)
    await page.waitForSelector("#setup-wizard", {
      state: "visible",
      timeout: 10_000,
    });
    await page.waitForTimeout(800); // let entrance animation finish
    await snap(page, "wizard-welcome", 1);
    timer.mark("wizard_shown");

    // ---------------------------------------------------------------
    // Step 1: Hardware Detection
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 1);

    // Trigger hardware detection (show() doesn't auto-run step logic)
    await page.evaluate(() => {
      if (typeof setupWizard !== "undefined") setupWizard.runStep1();
    });

    // Wait for hardware detection to complete ([OK] replaces loading spinner)
    await page.waitForFunction(
      () => {
        const el = document.getElementById("setup-hw-result");
        return el && (el.textContent?.includes("[OK]") || el.textContent?.includes("[X]"));
      },
      null,
      { timeout: 30_000 }
    );
    await page.waitForTimeout(600);
    await snap(page, "hardware-detected", 2);
    timer.mark("hardware_detected");

    // Click Next
    await page.click("#setup-next-btn");
    await page.waitForTimeout(400);

    // ---------------------------------------------------------------
    // Step 2: Ollama Check (auto-runs)
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 2);

    // Wait for Ollama check to complete ([OK] or error replaces loading)
    await page.waitForFunction(
      () => {
        const el = document.getElementById("setup-ollama-result");
        return el && (el.textContent?.includes("[OK]") || el.textContent?.includes("[X]") || el.textContent?.includes("model"));
      },
      null,
      { timeout: 30_000 }
    );
    await page.waitForTimeout(600);
    await snap(page, "ollama-found", 3);
    timer.mark("ollama_checked");

    // Click Next
    await page.click("#setup-next-btn");
    await page.waitForTimeout(400);

    // ---------------------------------------------------------------
    // Step 3: Model Download — already downloaded, so it should show ready
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 3);
    await page.waitForTimeout(1000);

    // Model should already be present — look for "ready" state or download btn
    const downloadBtn = page.locator("#setup-download-btn");
    const modelReady = page.locator(
      ".setup-wizard__status--ok"
    );

    // If model isn't already shown as ready, click download (should be instant)
    if (await downloadBtn.isVisible()) {
      await downloadBtn.click();
      // Wait for completion (model already cached, should be fast)
      await page.waitForSelector(".setup-wizard__status--ok", {
        state: "visible",
        timeout: 30_000,
      });
    }

    await page.waitForTimeout(600);
    await snap(page, "model-ready", 4);
    timer.mark("model_ready");

    // Click Next
    await page.click("#setup-next-btn");
    await page.waitForTimeout(400);

    // ---------------------------------------------------------------
    // Step 4: Verify — run inference test
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 4);

    // Click verify button
    const verifyBtn = page.locator("#setup-verify-btn");
    if (await verifyBtn.isVisible()) {
      await verifyBtn.click();

      // Wait for verification to complete
      await page.waitForSelector("#setup-verify-result", {
        state: "visible",
        timeout: 60_000,
      });
    }

    await page.waitForTimeout(800);
    await snap(page, "verified", 5);
    timer.mark("inference_verified");

    // Click Next
    await page.click("#setup-next-btn");
    await page.waitForTimeout(400);

    // ---------------------------------------------------------------
    // Steps 5-7: Skip optional steps (Content, MCP, Cloud)
    // ---------------------------------------------------------------
    for (const step of [5, 6, 7]) {
      await waitForSetupStep(page, step);
      await page.waitForTimeout(300);

      // On the last step, click Finish instead of Skip
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

      await page.waitForTimeout(400);
    }

    await snap(page, "wizard-done", 6);
    timer.mark("wizard_complete");

    // Wait for wizard to close
    await page.waitForSelector("#setup-wizard", {
      state: "hidden",
      timeout: 5_000,
    });
    await page.waitForTimeout(800);

    // ---------------------------------------------------------------
    // Create a channel
    // ---------------------------------------------------------------
    await snap(page, "dashboard-clean", 7);

    // Click the "+" button to create a channel
    await page.click("#add-channel-btn");
    await page.waitForSelector("#create-channel-modal", {
      state: "visible",
      timeout: 5_000,
    });
    await page.waitForTimeout(400);

    // Type channel name with human-like timing
    await typeHuman(page, "#new-channel-name", "demo-first-chat", {
      delay: 70,
      jitter: 35,
    });
    await page.waitForTimeout(300);
    await snap(page, "create-channel", 8);

    // Submit the form
    await page.click('#create-channel-form button[type="submit"]');
    await page.waitForTimeout(1000);
    timer.mark("channel_created");

    // ---------------------------------------------------------------
    // Navigate to the new channel (should auto-switch, or click it)
    // ---------------------------------------------------------------
    // Try clicking the new channel in the sidebar
    const channelLink = page.locator(
      '#channel-list li:has-text("demo-first-chat")'
    );
    if (await channelLink.isVisible()) {
      await channelLink.click();
      await page.waitForTimeout(500);
    }

    // Verify we're in the chat panel
    await page.waitForSelector("#panel-chat", {
      state: "visible",
      timeout: 5_000,
    });
    await page.waitForTimeout(500);
    await snap(page, "channel-ready", 9);

    // ---------------------------------------------------------------
    // Send the first message
    // ---------------------------------------------------------------
    // Short prompt = short response. This is the "hello world" of agent chat:
    // proves inference works, agent has personality, pipeline is live.
    // @mention triggers agent routing so we actually get a response.
    const firstMessage =
      "@python_developer Hello! Can you introduce yourself in one sentence?";

    await typeInContentEditable(page, "#message-input", firstMessage, {
      delay: 55,
      jitter: 30,
    });
    await page.waitForTimeout(500);
    await snap(page, "message-typed", 10);

    // Click send
    await page.click("#send-btn");
    timer.mark("message_sent");
    await page.waitForTimeout(500);

    // Wait for agent response
    await waitForAgentResponse(page, 90_000);
    await snap(page, "first-response", 11);
    timer.mark("response_received");

    // Final dashboard shot
    await page.waitForTimeout(1000);
    await snap(page, "conversation-active", 12);
    timer.mark("demo_complete");

    // ---------------------------------------------------------------
    // Output timing summary
    // ---------------------------------------------------------------
    const summary = timer.summary();
    console.log("\n========================================");
    console.log("  WEB UI DEMO TIMING");
    console.log("========================================");
    console.log(summary);
    console.log("========================================\n");

    timer.save("recordings/web-ui-timing.json");
  });
});
