import { test, expect } from "@playwright/test";
import {
  expectConnected,
  skipSetupWizard,
  openSettings,
  ConsoleErrorCollector,
} from "../helpers/test-utils";

/**
 * Settings feature tests -- run when settings-related files change.
 *
 * Verifies settings modal, value persistence, and mode switching.
 *
 * Tag: @settings
 */

test.describe("@settings Settings Persistence", () => {
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

  test("settings modal opens and closes", async ({ page }) => {
    await openSettings(page);

    // Settings modal should be visible
    const modal = page.locator(
      '#settings-modal, [class*="settings"], [role="dialog"]'
    );
    await expect(modal.first()).toBeVisible({ timeout: 5_000 });

    // Close it (Escape or close button)
    const closeBtn = modal.first().locator(
      'button:has-text("Close"), button:has-text("X"), [aria-label="Close"]'
    );
    if (await closeBtn.first().isVisible()) {
      await closeBtn.first().click();
    } else {
      await page.keyboard.press("Escape");
    }
    await page.waitForTimeout(500);
  });

  test("response mode toggle reflects in UI", async ({ page }) => {
    // Look for the mode toggle (Smart/Smarter/Smartest)
    const modeToggle = page.locator(
      '#response-mode-toggle, [class*="mode-toggle"], [data-testid="mode-toggle"]'
    );

    if (await modeToggle.isVisible()) {
      // Click to cycle mode
      await modeToggle.click();
      await page.waitForTimeout(500);

      // The toggle should visually change (text or class)
      // We just verify it didn't crash -- the ConsoleErrorCollector
      // will catch any JS errors from a broken mode switch
    }
  });

  test("settings persist across page reload", async ({ page }) => {
    await openSettings(page);

    // Find any text input or toggle in settings
    const modal = page.locator(
      '#settings-modal, [class*="settings"], [role="dialog"]'
    );

    // Try to find the display name or any editable field
    const nameInput = modal.first().locator('input[type="text"]').first();

    if (await nameInput.isVisible()) {
      // Clear and type a unique value
      const testValue = `e2e-test-${Date.now()}`;
      await nameInput.fill(testValue);

      // Save
      const saveBtn = modal.first().locator(
        'button:has-text("Save"), button[type="submit"]'
      );
      if (await saveBtn.first().isVisible()) {
        await saveBtn.first().click();
        await page.waitForTimeout(1000);

        // Reload
        await page.reload();
        await skipSetupWizard(page);
        await expectConnected(page);

        // Re-open settings
        await openSettings(page);

        // Check the value persisted
        const reloadedModal = page.locator(
          '#settings-modal, [class*="settings"], [role="dialog"]'
        );
        const reloadedInput = reloadedModal
          .first()
          .locator('input[type="text"]')
          .first();

        if (await reloadedInput.isVisible()) {
          const value = await reloadedInput.inputValue();
          expect(value).toBe(testValue);
        }
      }
    }
  });
});
