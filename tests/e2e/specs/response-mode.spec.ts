/**
 * @response-mode -- Response mode toggle (Smart/Smarter/Smartest) tests.
 *
 * Verifies the mode button exists in chat, toggles on click,
 * and maintains per-channel state.
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
  createChannel,
} from "../helpers/test-utils";

test.describe("@response-mode Response Mode Toggle", () => {
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

  test("response mode button visible in chat", async ({ page }) => {
    const channelName = `mode-test-${Date.now()}`;
    await createChannel(page, channelName);
    const modeBtn = page.locator("#response-mode-btn");
    await expect(modeBtn).toBeVisible({ timeout: 5_000 });
  });

  test("mode button has text content", async ({ page }) => {
    const channelName = `mode-text-${Date.now()}`;
    await createChannel(page, channelName);
    const modeBtn = page.locator("#response-mode-btn");
    const text = await modeBtn.textContent();
    // Should contain one of the mode indicators
    expect(text).toMatch(/\[S\]|\[S\+\]|\[S\+\+\]/);
  });

  test("toggle cycles mode on click", async ({ page }) => {
    const channelName = `mode-cycle-${Date.now()}`;
    await createChannel(page, channelName);
    const modeBtn = page.locator("#response-mode-btn");

    const textBefore = await modeBtn.textContent();
    await modeBtn.click();
    await page.waitForTimeout(300);
    const textAfter = await modeBtn.textContent();

    // Text should change after click (mode cycled)
    expect(textAfter).not.toBe(textBefore);
  });

  test("mode persists within a channel", async ({ page }) => {
    const channelName = `mode-persist-${Date.now()}`;
    await createChannel(page, channelName);
    const modeBtn = page.locator("#response-mode-btn");

    // Click to change mode
    await modeBtn.click();
    await page.waitForTimeout(300);
    const modeAfterClick = await modeBtn.textContent();

    // Switch away and back
    await page.click('button[data-panel="team"]');
    await page.waitForTimeout(300);
    await page.click(`#channel-list li:has-text("${channelName}")`);
    await page.waitForTimeout(500);

    const modeAfterReturn = await page.locator("#response-mode-btn").textContent();
    expect(modeAfterReturn).toBe(modeAfterClick);
  });
});
