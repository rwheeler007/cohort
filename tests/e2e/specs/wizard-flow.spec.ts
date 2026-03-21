import { test, expect, Page } from "@playwright/test";
import { ConsoleErrorCollector } from "../helpers/test-utils";

/**
 * Setup Wizard flow -- verify all 8 steps are reachable including Security.
 *
 * Tag: @wizard
 *
 * Tests that the wizard navigates through to the Security step (step 8)
 * and that skipping it applies readonly as the secure default.
 */

let errors: ConsoleErrorCollector;

test.beforeEach(async ({ page }) => {
  errors = new ConsoleErrorCollector();
  errors.attach(page);
  await page.goto("/");
});

test.describe("@wizard Setup Wizard Flow", () => {
  test("wizard navigates through all 8 steps to security", async ({ page }) => {
    const wizard = page.locator("#setup-wizard");
    await wizard.waitFor({ state: "visible", timeout: 15_000 });

    // Wait for step 1 to render
    await page.waitForSelector('.setup-wizard__step[data-step="1"]', {
      state: "visible",
      timeout: 15_000,
    });
    await page.waitForTimeout(2_000);

    // Skip through steps 1-7
    for (let step = 1; step <= 7; step++) {
      const stepEl = page.locator(`.setup-wizard__step[data-step="${step}"]`);
      await stepEl.waitFor({ state: "visible", timeout: 10_000 });
      await page.waitForTimeout(1_000);

      const skipBtn = page.locator("#setup-skip-btn");
      if (await skipBtn.isVisible()) {
        await skipBtn.click();
        await page.waitForTimeout(1_500);
      }
    }

    // Step 8: Security should now be visible
    const securityStep = page.locator('.setup-wizard__step[data-step="8"]');
    await securityStep.waitFor({ state: "visible", timeout: 10_000 });

    // Verify security profile options exist
    const readonlyRadio = page.locator('input[name="setup-security-profile"][value="readonly"]');
    const developerRadio = page.locator('input[name="setup-security-profile"][value="developer"]');
    const minimalRadio = page.locator('input[name="setup-security-profile"][value="minimal"]');

    await expect(readonlyRadio).toBeVisible();
    await expect(developerRadio).toBeVisible();
    await expect(minimalRadio).toBeVisible();

    // Readonly should be checked by default
    await expect(readonlyRadio).toBeChecked();

    // Max turns slider should exist
    const turnsSlider = page.locator("#setup-max-turns");
    await expect(turnsSlider).toBeVisible();

    // Deny paths textarea should exist
    const denyPaths = page.locator("#setup-deny-paths");
    await expect(denyPaths).toBeVisible();

    // Finish button should be visible on final step
    const finishBtn = page.locator("#setup-finish-btn");
    await expect(finishBtn).toBeVisible();
  });

  test("step 8 security indicator shows in step nav", async ({ page }) => {
    const wizard = page.locator("#setup-wizard");
    await wizard.waitFor({ state: "visible", timeout: 15_000 });
    await page.waitForTimeout(2_000);

    const stepInd = page.locator("#step-ind-8");
    await expect(stepInd).toBeVisible();
    await expect(stepInd).toHaveText("8");

    // Label should say Security
    const stepBtn = page.locator('button[data-setup-step="8"]');
    await expect(stepBtn).toContainText("Security");
  });

  test("skipping security step applies readonly default", async ({ page }) => {
    const wizard = page.locator("#setup-wizard");
    await wizard.waitFor({ state: "visible", timeout: 15_000 });
    await page.waitForTimeout(2_000);

    // Skip all steps
    for (let i = 0; i < 10; i++) {
      await page.waitForTimeout(1_000);
      const finishBtn = page.locator("#setup-finish-btn");
      const skipBtn = page.locator("#setup-skip-btn");

      if (await finishBtn.isVisible()) {
        await finishBtn.click();
        break;
      } else if (await skipBtn.isVisible()) {
        await skipBtn.click();
        await page.waitForTimeout(1_500);
      }
    }

    // Wizard should be hidden after finish
    await expect(wizard).toBeHidden({ timeout: 10_000 });

    // Verify settings were saved with readonly default
    const resp = await page.evaluate(async () => {
      const r = await fetch("/api/settings");
      return r.json();
    });

    expect(resp.default_permissions).toBeDefined();
    expect(resp.default_permissions.profile).toBe("readonly");
  });
});
