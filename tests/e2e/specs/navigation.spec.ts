/**
 * @navigation -- Panel navigation and sidebar interaction tests.
 *
 * Verifies every main panel is reachable via sidebar buttons,
 * footer buttons work, and channel clicks switch to chat.
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
  createChannel,
  switchToPanel,
  expectPanelActive,
} from "../helpers/test-utils";

test.describe("@navigation Panel Navigation", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await skipSetupWizard(page);
    await expectConnected(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("sidebar nav buttons switch to tasks panel", async ({ page }) => {
    await switchToPanel(page, "tasks");
    await expectPanelActive(page, "panel-tasks");
    // Team panel should be hidden
    await expect(page.locator("#panel-team")).not.toBeVisible();
  });

  test("sidebar nav buttons switch to review panel", async ({ page }) => {
    await switchToPanel(page, "output");
    await expectPanelActive(page, "panel-output");
    await expect(page.locator("#panel-team")).not.toBeVisible();
  });

  test("sidebar nav buttons switch back to team panel", async ({ page }) => {
    // Go to tasks, then back to team
    await switchToPanel(page, "tasks");
    await expectPanelActive(page, "panel-tasks");
    await switchToPanel(page, "team");
    await expectPanelActive(page, "panel-team");
  });

  test("clicking channel switches to chat panel", async ({ page }) => {
    const channelName = `nav-test-${Date.now()}`;
    await createChannel(page, channelName);
    // After creating, chat panel should be active
    await expectPanelActive(page, "panel-chat");
  });

  test("footer buttons are clickable without crash", async ({ page }) => {
    // Refresh button
    const refreshBtn = page.locator("#refresh-btn");
    if (await refreshBtn.isVisible()) {
      await refreshBtn.click();
      await page.waitForTimeout(500);
    }

    // Settings button
    const settingsBtn = page.locator("#settings-btn");
    if (await settingsBtn.isVisible()) {
      await settingsBtn.click();
      await page.waitForTimeout(300);
      // Close settings if it opened
      const closeBtn = page.locator("#settings-modal .modal__close, #settings-close");
      if (await closeBtn.first().isVisible()) {
        await closeBtn.first().click();
      }
    }
  });

  test("all three nav buttons exist and are visible", async ({ page }) => {
    await expect(page.locator('button[data-panel="team"]')).toBeVisible();
    await expect(page.locator('button[data-panel="tasks"]')).toBeVisible();
    await expect(page.locator('button[data-panel="output"]')).toBeVisible();
  });
});
