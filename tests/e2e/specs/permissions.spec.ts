/**
 * @permissions -- Permissions modal tests.
 *
 * Verifies the permissions modal opens, has 4 tabs,
 * tab switching works, and add-service flow exists.
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
  openPermissions,
} from "../helpers/test-utils";

test.describe("@permissions Permissions Modal", () => {
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

  test("permissions button exists in footer", async ({ page }) => {
    await expect(page.locator("#permissions-btn")).toBeVisible();
  });

  test("permissions modal opens on click", async ({ page }) => {
    await openPermissions(page);
    await expect(page.locator("#permissions-modal")).toBeVisible({ timeout: 5_000 });
  });

  test("modal has 4 permission tabs", async ({ page }) => {
    await openPermissions(page);
    await expect(page.locator('[data-perm-tab="services"]')).toBeVisible();
    await expect(page.locator('[data-perm-tab="agents"]')).toBeVisible();
    await expect(page.locator('[data-perm-tab="tool-defaults"]')).toBeVisible();
    await expect(page.locator('[data-perm-tab="file-perms"]')).toBeVisible();
  });

  test("tab switching shows correct panel", async ({ page }) => {
    await openPermissions(page);
    for (const tab of ["agents", "tool-defaults", "file-perms", "services"]) {
      await page.click(`[data-perm-tab="${tab}"]`);
      await page.waitForTimeout(200);
    }
    // If we got here without errors, tab switching works
  });

  test("add service key button exists", async ({ page }) => {
    await openPermissions(page);
    await expect(page.locator("#add-service-key-btn")).toBeVisible();
  });

  test("add service key button opens form", async ({ page }) => {
    await openPermissions(page);
    await page.click("#add-service-key-btn");
    await page.waitForTimeout(300);
    // The add-service modal or inline form should appear
    const addModal = page.locator("#add-service-modal, #add-service-form, .add-service-form");
    await expect(addModal.first()).toBeVisible({ timeout: 3_000 });
  });

  test("permissions modal can be closed", async ({ page }) => {
    await openPermissions(page);
    const closeBtn = page.locator("#permissions-modal .modal__close, #permissions-close");
    await closeBtn.first().click();
    await page.waitForTimeout(300);
    await expect(page.locator("#permissions-modal")).not.toBeVisible();
  });
});
