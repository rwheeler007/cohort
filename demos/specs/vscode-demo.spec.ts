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
 * VS Code Extension Demo: "Zero to Conversation"
 *
 * Same flow as web-ui-demo but at 1280x800 viewport to simulate
 * the VS Code webview panel. Uses the same Cohort web dashboard
 * since the extension embeds it via vscode-bridge.js.
 *
 * For the marketing page, this is visually indistinguishable from
 * the real extension — same HTML, same CSS, same wizard.
 *
 * Prerequisites:
 *   - Ollama running with model downloaded
 *   - Cohort server running
 */

const timer = new DemoTimer();

test.describe("VS Code Extension - Zero to Conversation", () => {
  test.beforeAll(async () => {
    await resetToFirstRun({ vscode: true });
  });

  test.afterAll(async () => {
    await restoreFromBackup();
  });

  test("extension setup and first message", async ({ page }) => {
    timer.start();

    // ---------------------------------------------------------------
    // Load page — compact viewport mimics VS Code panel
    // ---------------------------------------------------------------
    await page.goto("/");
    await waitForConnection(page);
    timer.mark("page_loaded");

    // Wait for setup wizard
    await page.waitForSelector("#setup-wizard", {
      state: "visible",
      timeout: 10_000,
    });
    await page.waitForTimeout(800);
    await snap(page, "vsc-wizard-welcome", 1);
    timer.mark("wizard_shown");

    // ---------------------------------------------------------------
    // Step 1: Hardware Detection
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 1);
    await page.waitForFunction(
      () => {
        const el = document.getElementById("setup-hw-result");
        return el && !el.textContent?.includes("Detecting");
      },
      null,
      { timeout: 15_000 }
    );
    await page.waitForTimeout(500);
    await snap(page, "vsc-hardware", 2);
    timer.mark("hardware_detected");
    await page.click("#setup-next-btn");
    await page.waitForTimeout(300);

    // ---------------------------------------------------------------
    // Step 2: Ollama Check
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 2);
    await page.waitForFunction(
      () => {
        const el = document.getElementById("setup-ollama-result");
        return el && !el.textContent?.includes("Checking");
      },
      null,
      { timeout: 15_000 }
    );
    await page.waitForTimeout(500);
    await snap(page, "vsc-ollama", 3);
    timer.mark("ollama_checked");
    await page.click("#setup-next-btn");
    await page.waitForTimeout(300);

    // ---------------------------------------------------------------
    // Step 3: Model (already downloaded)
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 3);
    await page.waitForTimeout(800);

    const downloadBtn = page.locator("#setup-download-btn");
    if (await downloadBtn.isVisible()) {
      await downloadBtn.click();
      await page.waitForSelector(".setup-wizard__status--ok", {
        state: "visible",
        timeout: 30_000,
      });
    }
    await snap(page, "vsc-model", 4);
    timer.mark("model_ready");
    await page.click("#setup-next-btn");
    await page.waitForTimeout(300);

    // ---------------------------------------------------------------
    // Step 4: Verify inference
    // ---------------------------------------------------------------
    await waitForSetupStep(page, 4);
    const verifyBtn = page.locator("#setup-verify-btn");
    if (await verifyBtn.isVisible()) {
      await verifyBtn.click();
      await page.waitForSelector("#setup-verify-result", {
        state: "visible",
        timeout: 60_000,
      });
    }
    await page.waitForTimeout(500);
    await snap(page, "vsc-verified", 5);
    timer.mark("inference_verified");
    await page.click("#setup-next-btn");
    await page.waitForTimeout(300);

    // ---------------------------------------------------------------
    // Steps 5-7: Skip optional
    // ---------------------------------------------------------------
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
      await page.waitForTimeout(300);
    }

    timer.mark("wizard_complete");
    await page.waitForSelector("#setup-wizard", {
      state: "hidden",
      timeout: 5_000,
    });
    await page.waitForTimeout(600);
    await snap(page, "vsc-dashboard", 6);

    // ---------------------------------------------------------------
    // Create channel
    // ---------------------------------------------------------------
    await page.click("#add-channel-btn");
    await page.waitForSelector("#create-channel-modal", {
      state: "visible",
      timeout: 5_000,
    });
    await page.waitForTimeout(300);

    await typeHuman(page, "#new-channel-name", "demo-first-chat", {
      delay: 60,
      jitter: 30,
    });
    await snap(page, "vsc-create-channel", 7);
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
    await snap(page, "vsc-channel-ready", 8);

    // ---------------------------------------------------------------
    // First message
    // ---------------------------------------------------------------
    // Short "hello world" prompt — proves pipeline works, gets a fast 1-2 sentence reply
    const msg = "Hello! Can you introduce yourself in one sentence?";

    await typeInContentEditable(page, "#message-input", msg, {
      delay: 50,
      jitter: 25,
    });
    await snap(page, "vsc-message-typed", 9);

    await page.click("#send-btn");
    timer.mark("message_sent");

    await waitForAgentResponse(page, 90_000);
    await snap(page, "vsc-first-response", 10);
    timer.mark("response_received");

    await page.waitForTimeout(800);
    await snap(page, "vsc-conversation", 11);
    timer.mark("demo_complete");

    // ---------------------------------------------------------------
    // Timing
    // ---------------------------------------------------------------
    console.log("\n========================================");
    console.log("  VS CODE DEMO TIMING");
    console.log("========================================");
    console.log(timer.summary());
    console.log("========================================\n");

    timer.save("recordings/vscode-timing.json");
  });
});
