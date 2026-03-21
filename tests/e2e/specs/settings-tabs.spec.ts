/**
 * @settings-tabs -- Deep settings modal tab coverage.
 *
 * Extends existing settings.spec.ts with tab switching,
 * field presence for all 4 tabs (General, Connections, Model Tiers, Data).
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
  openSettings,
} from "../helpers/test-utils";

test.describe("@settings-tabs Settings Modal Tabs", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await skipSetupWizard(page);
    await expectConnected(page);
    await openSettings(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("settings modal has 4 tab buttons", async ({ page }) => {
    await expect(page.locator('[data-settings-tab="general"]')).toBeVisible();
    await expect(page.locator('[data-settings-tab="connections"]')).toBeVisible();
    await expect(page.locator('[data-settings-tab="model-tiers"]')).toBeVisible();
    await expect(page.locator('[data-settings-tab="data"]')).toBeVisible();
  });

  test("general tab has identity fields", async ({ page }) => {
    // General tab is active by default
    const nameInput = page.locator("#settings-user-name");
    const roleInput = page.locator("#settings-user-role");
    await expect(nameInput).toBeVisible({ timeout: 3_000 });
    await expect(roleInput).toBeVisible({ timeout: 3_000 });
  });

  test("connections tab shows cloud provider", async ({ page }) => {
    await page.click('[data-settings-tab="connections"]');
    await page.waitForTimeout(300);
    const panel = page.locator('[data-settings-panel="connections"]');
    await expect(panel).toBeVisible({ timeout: 3_000 });
    const provider = page.locator("#settings-cloud-provider");
    await expect(provider).toBeAttached();
  });

  test("model tiers tab has tier inputs", async ({ page }) => {
    await page.click('[data-settings-tab="model-tiers"]');
    await page.waitForTimeout(300);
    const panel = page.locator('[data-settings-panel="model-tiers"]');
    await expect(panel).toBeVisible({ timeout: 3_000 });
    await expect(page.locator("#settings-tier-smart-primary")).toBeAttached();
    await expect(page.locator("#settings-tier-smarter-primary")).toBeAttached();
    await expect(page.locator("#settings-tier-smartest-primary")).toBeAttached();
  });

  test("budget inputs exist on model tiers tab", async ({ page }) => {
    await page.click('[data-settings-tab="model-tiers"]');
    await page.waitForTimeout(300);
    await expect(page.locator("#settings-budget-daily")).toBeAttached();
    await expect(page.locator("#settings-budget-monthly")).toBeAttached();
  });

  test("data tab loads", async ({ page }) => {
    await page.click('[data-settings-tab="data"]');
    await page.waitForTimeout(300);
    const panel = page.locator('[data-settings-panel="data"]');
    await expect(panel).toBeVisible({ timeout: 3_000 });
  });

  test("dev mode toggle exists on connections tab", async ({ page }) => {
    await page.click('[data-settings-tab="connections"]');
    await page.waitForTimeout(300);
    const devMode = page.locator("#settings-dev-mode");
    await expect(devMode).toBeAttached();
  });

  test("tab switching shows correct panel", async ({ page }) => {
    // Click each tab and verify the panel switches
    for (const tab of ["connections", "model-tiers", "data", "general"]) {
      await page.click(`[data-settings-tab="${tab}"]`);
      await page.waitForTimeout(200);
      const panel = page.locator(`[data-settings-panel="${tab}"]`);
      await expect(panel).toBeVisible({ timeout: 3_000 });
    }
  });
});
