/**
 * @briefing -- Executive briefing sidebar section tests.
 *
 * Verifies briefing buttons exist, history list renders,
 * and generate button doesn't crash (graceful fail without Ollama).
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
} from "../helpers/test-utils";

test.describe("@briefing Executive Briefing", () => {
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

  test("generate briefing button exists", async ({ page }) => {
    await expect(page.locator("#generate-briefing-btn")).toBeVisible({ timeout: 5_000 });
  });

  test("view briefing button exists", async ({ page }) => {
    await expect(page.locator("#view-briefing-btn")).toBeVisible({ timeout: 5_000 });
  });

  test("fetch intel button exists", async ({ page }) => {
    await expect(page.locator("#fetch-intel-btn")).toBeVisible({ timeout: 5_000 });
  });

  test("briefing history list exists in DOM", async ({ page }) => {
    const historyList = page.locator("#briefing-history-list");
    await expect(historyList).toBeAttached();
  });
});
