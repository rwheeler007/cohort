/**
 * @review -- Review/Output panel and review modal tests.
 *
 * Verifies the review panel renders, empty state shows,
 * and the review modal structure is correct.
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
  switchToPanel,
  expectPanelActive,
} from "../helpers/test-utils";

test.describe("@review Review Panel", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await skipSetupWizard(page);
    await expectConnected(page);
    await switchToPanel(page, "output");
  });

  test.afterEach(() => {
    errors.check();
  });

  test("review panel renders", async ({ page }) => {
    await expectPanelActive(page, "panel-output");
  });

  test("empty state shown on fresh install", async ({ page }) => {
    const empty = page.locator("#output-empty");
    await expect(empty).toBeVisible({ timeout: 5_000 });
  });

  test("output list container exists", async ({ page }) => {
    const outputList = page.locator("#output-list");
    await expect(outputList).toBeAttached();
  });

  test("review modal exists in DOM", async ({ page }) => {
    const modal = page.locator("#review-modal");
    await expect(modal).toBeAttached();
  });

  test("review modal has notes textarea", async ({ page }) => {
    const notes = page.locator("#review-notes-input");
    await expect(notes).toBeAttached();
  });

  test("review submit button exists and starts disabled", async ({ page }) => {
    const submit = page.locator("#review-submit");
    await expect(submit).toBeAttached();
    await expect(submit).toBeDisabled();
  });
});
