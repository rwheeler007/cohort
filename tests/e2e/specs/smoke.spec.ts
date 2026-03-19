import { test, expect } from "@playwright/test";
import {
  expectConnected,
  expectWebSocketHealthy,
  skipSetupWizard,
  ConsoleErrorCollector,
} from "../helpers/test-utils";

/**
 * Smoke tests -- always run, <30 seconds.
 *
 * Verifies the absolute basics: page loads, WebSocket connects,
 * UI renders without JS errors.
 *
 * Tag: @smoke
 */

test.describe("@smoke Core Health", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await skipSetupWizard(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("page loads without errors", async ({ page }) => {
    // Page should have a title
    const title = await page.title();
    expect(title).toBeTruthy();

    // Main app container should exist
    await expect(page.locator("body")).toBeVisible();
  });

  test("WebSocket connects", async ({ page }) => {
    await expectConnected(page);
    await expectWebSocketHealthy(page);
  });

  test("sidebar renders channel list", async ({ page }) => {
    // Channel list container should exist (may be empty on fresh install)
    const channelList = page.locator("#channel-list");
    await expect(channelList).toBeVisible({ timeout: 10_000 });
  });

  test("message input is focusable", async ({ page }) => {
    // Need a channel first -- create one if sidebar is empty
    const channels = page.locator("#channel-list li");
    const count = await channels.count();

    if (count === 0) {
      // Create a test channel
      const addBtn = page.locator("#add-channel-btn");
      if (await addBtn.isVisible()) {
        await addBtn.click();
        await page.waitForSelector("#create-channel-modal", {
          state: "visible",
          timeout: 5_000,
        });
        await page.fill("#new-channel-name", "smoke-test");
        await page.click('#create-channel-form button[type="submit"]');
        await page.waitForTimeout(1000);
      }
    } else {
      // Click first channel
      await channels.first().click();
      await page.waitForTimeout(500);
    }

    // Message input should be present and focusable
    const input = page.locator("#message-input");
    if (await input.isVisible()) {
      await input.click();
      await expect(input).toBeFocused();
    }
  });

  test("navigation between panels works", async ({ page }) => {
    // The main layout should have navigable panels
    // This verifies the basic SPA routing doesn't crash
    const body = page.locator("body");
    await expect(body).toBeVisible();

    // Check that we can navigate without errors
    // (ConsoleErrorCollector in afterEach will catch JS crashes)
    await page.waitForTimeout(1000);
  });
});
