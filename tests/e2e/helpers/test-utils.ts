/**
 * E2E Test Utilities for Cohort
 *
 * Assertion-focused helpers that reuse patterns from demos/helpers/demo-utils.ts
 * but swap screenshots/video for expect() assertions.
 *
 * Key design rule: these tests run against an ISOLATED Cohort instance
 * with its own temp data directory. They never touch production data.
 */

import { Page, expect, Locator } from "@playwright/test";

// =====================================================================
// Connection & Page Health
// =====================================================================

/**
 * Wait for WebSocket connection to establish.
 * Asserts the connection status indicator shows connected.
 */
export async function expectConnected(page: Page, timeoutMs = 15_000): Promise<void> {
  await page.waitForFunction(
    () => {
      const el = document.getElementById("connection-status");
      return el && el.textContent?.toLowerCase().includes("connect");
    },
    null,
    { timeout: timeoutMs }
  );
}

/**
 * Collect JS console errors during a test.
 * Call at test start, check at end with expectNoJSErrors().
 */
export class ConsoleErrorCollector {
  private errors: string[] = [];

  attach(page: Page): void {
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        this.errors.push(msg.text());
      }
    });
  }

  /** Assert no JS errors were logged. Ignores known benign errors. */
  check(): void {
    const real = this.errors.filter(
      (e) =>
        // Ignore favicon 404s and similar noise
        !e.includes("favicon") &&
        !e.includes("net::ERR_") &&
        !e.includes("ResizeObserver") &&
        !e.includes("404 (Not Found)") &&
        !e.includes("Failed to load resource")
    );
    if (real.length > 0) {
      throw new Error(
        `[X] ${real.length} JS console error(s):\n${real.join("\n")}`
      );
    }
  }
}

// =====================================================================
// Message Assertions
// =====================================================================

/**
 * Wait for a new message to appear in the message list.
 * Returns the locator for the new message element.
 */
export async function waitForNewMessage(
  page: Page,
  timeoutMs = 60_000
): Promise<void> {
  const initialCount = await page.locator("#messages-list .message").count();

  await page.waitForFunction(
    (prevCount) => {
      const msgs = document.querySelectorAll("#messages-list .message");
      return msgs.length > prevCount;
    },
    initialCount,
    { timeout: timeoutMs }
  );

  // Let streaming complete
  await page.waitForTimeout(1500);
}

/**
 * Assert that a message from a specific agent exists in the message list.
 */
export async function expectMessageFrom(
  page: Page,
  agentName: string
): Promise<void> {
  const messageList = page.locator("#messages-list");
  // Agent messages typically have the agent name in a sender element
  const agentMessage = messageList.locator(
    `.message:has-text("${agentName}")`
  );
  await expect(agentMessage.first()).toBeVisible({ timeout: 5_000 });
}

/**
 * Assert the latest message contains specific text.
 */
export async function expectLatestMessageContains(
  page: Page,
  text: string
): Promise<void> {
  const messages = page.locator("#messages-list .message");
  const lastMessage = messages.last();
  await expect(lastMessage).toContainText(text, { timeout: 5_000 });
}

// =====================================================================
// Channel Assertions
// =====================================================================

/**
 * Assert a channel exists in the sidebar channel list.
 */
export async function expectChannelExists(
  page: Page,
  channelName: string
): Promise<void> {
  const channelList = page.locator("#channel-list");
  const channel = channelList.locator(`li:has-text("${channelName}")`);
  await expect(channel.first()).toBeVisible({ timeout: 5_000 });
}

/**
 * Create a channel via the UI and assert it appears.
 */
export async function createChannel(
  page: Page,
  name: string
): Promise<void> {
  await page.click("#add-channel-btn");
  await page.waitForSelector("#create-channel-modal", {
    state: "visible",
    timeout: 5_000,
  });

  await page.fill("#new-channel-name", name);
  await page.click('#create-channel-form button[type="submit"]');
  await page.waitForTimeout(1000);

  // Verify it appeared in the sidebar
  await expectChannelExists(page, name);
}

/**
 * Switch to a channel by clicking it in the sidebar.
 */
export async function switchToChannel(
  page: Page,
  channelName: string
): Promise<void> {
  const channelLink = page.locator(
    `#channel-list li:has-text("${channelName}")`
  );
  await channelLink.first().click();
  await page.waitForTimeout(500);
}

// =====================================================================
// Message Input
// =====================================================================

/**
 * Type a message and send it (fast, no human-like delays).
 */
export async function sendMessage(
  page: Page,
  message: string
): Promise<void> {
  const input = page.locator("#message-input");
  await input.click();

  // Clear existing content
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");

  // Type the message
  await input.type(message, { delay: 0 });
  await page.click("#send-btn");
  await page.waitForTimeout(300);
}

// =====================================================================
// Settings Assertions
// =====================================================================

/**
 * Open the settings modal.
 */
export async function openSettings(page: Page): Promise<void> {
  // Settings button may be a gear icon or text
  const settingsBtn = page.locator(
    'button:has-text("Settings"), #settings-btn, [aria-label="Settings"]'
  );
  await settingsBtn.first().click();
  await page.waitForTimeout(500);
}

/**
 * Assert the WebSocket connection is healthy.
 */
export async function expectWebSocketHealthy(page: Page): Promise<void> {
  const status = page.locator("#connection-status");
  await expect(status).toContainText(/connect/i, { timeout: 10_000 });
}

// =====================================================================
// Setup Helpers
// =====================================================================

/**
 * Skip the setup wizard if it appears.
 * Since E2E tests run against a fresh data dir, the wizard will
 * always appear on first load. This helper dismisses it quickly.
 */
export async function skipSetupWizard(page: Page): Promise<void> {
  const wizard = page.locator("#setup-wizard");

  // Wait briefly for wizard -- it may not appear if already dismissed
  try {
    await wizard.waitFor({ state: "visible", timeout: 5_000 });
  } catch {
    return; // No wizard, nothing to skip
  }

  // Click through all steps using Skip/Next/Finish buttons
  const maxSteps = 10; // Server has 7 steps, VS Code has 9; safety margin for both
  for (let i = 0; i < maxSteps; i++) {
    const finish = page.locator("#setup-finish-btn");
    const skip = page.locator("#setup-skip-btn");
    const next = page.locator("#setup-next-btn");

    if (await finish.isVisible()) {
      await finish.click();
      break;
    } else if (await skip.isVisible()) {
      await skip.click();
    } else if (await next.isVisible()) {
      await next.click();
    }
    await page.waitForTimeout(300);
  }

  // Wait for wizard to close
  try {
    await wizard.waitFor({ state: "hidden", timeout: 5_000 });
  } catch {
    // Wizard might already be gone
  }
  await page.waitForTimeout(500);
}

// =====================================================================
// Panel Navigation
// =====================================================================

/**
 * Switch to a main panel via sidebar nav button.
 */
export async function switchToPanel(
  page: Page,
  panelName: "team" | "tasks" | "output"
): Promise<void> {
  await page.click(`button[data-panel="${panelName}"]`);
  await page.waitForTimeout(300);
}

/**
 * Assert a panel is the visible/active one.
 */
export async function expectPanelActive(
  page: Page,
  panelId: string
): Promise<void> {
  await expect(page.locator(`#${panelId}`)).toBeVisible({ timeout: 5_000 });
}

/**
 * Open the Assign Task modal (navigates to tasks panel first).
 */
export async function openAssignTaskModal(page: Page): Promise<void> {
  await switchToPanel(page, "tasks");
  await page.click("#assign-task-btn");
  await page.waitForSelector("#assign-task-modal:not([hidden])", {
    timeout: 5_000,
  });
}

/**
 * Open the Permissions modal.
 */
export async function openPermissions(page: Page): Promise<void> {
  await page.click("#permissions-btn");
  await page.waitForSelector("#permissions-modal:not([hidden])", {
    timeout: 5_000,
  });
}

// =====================================================================
// Setup Helpers
// =====================================================================

/**
 * Navigate the setup wizard to a specific step number (1-8).
 * Skips earlier steps by clicking the Skip button repeatedly.
 * The wizard must be visible before calling this.
 */
export async function navigateToWizardStep(
  page: Page,
  targetStep: number
): Promise<void> {
  const wizard = page.locator("#setup-wizard");
  await wizard.waitFor({ state: "visible", timeout: 5_000 });

  // Skip from step 1 to the target
  for (let i = 1; i < targetStep; i++) {
    const skip = page.locator("#setup-skip-btn");
    await skip.waitFor({ state: "visible", timeout: 3_000 });
    await skip.click();
    await page.waitForTimeout(400);
  }
}
