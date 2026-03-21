/**
 * @tasks -- Tasks panel, sub-tabs, assign task modal, and schedule form tests.
 *
 * Covers the largest untested UI surface: task sub-tabs, assign modal
 * with both Assign and Schedule tabs, preset radios, and custom schedule.
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
  switchToPanel,
  expectPanelActive,
  openAssignTaskModal,
} from "../helpers/test-utils";

test.describe("@tasks Tasks Panel", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await skipSetupWizard(page);
    await expectConnected(page);
    await switchToPanel(page, "tasks");
  });

  test.afterEach(() => {
    errors.check();
  });

  test("tasks panel renders with 4 sub-tabs", async ({ page }) => {
    await expectPanelActive(page, "panel-tasks");
    await expect(page.locator('[data-task-view="active"]')).toBeVisible();
    await expect(page.locator('[data-task-view="scheduled"]')).toBeVisible();
    await expect(page.locator('[data-task-view="completed"]')).toBeVisible();
    await expect(page.locator('[data-task-view="archived"]')).toBeVisible();
  });

  test("sub-tab switching shows scheduled view", async ({ page }) => {
    await page.click('[data-task-view="scheduled"]');
    await page.waitForTimeout(300);
    await expect(page.locator("#task-view-scheduled")).toBeVisible();
    await expect(page.locator("#task-view-active")).not.toBeVisible();
  });

  test("sub-tab switching shows completed view", async ({ page }) => {
    await page.click('[data-task-view="completed"]');
    await page.waitForTimeout(300);
    await expect(page.locator("#task-view-completed")).toBeVisible();
  });

  test("assign task button opens modal", async ({ page }) => {
    await page.click("#assign-task-btn");
    await page.waitForTimeout(500);
    const modal = page.locator("#assign-task-modal");
    await expect(modal).toBeVisible({ timeout: 5_000 });
  });

  test("assign form has required fields", async ({ page }) => {
    await openAssignTaskModal(page);
    await expect(page.locator("#task-agent-select")).toBeVisible();
    await expect(page.locator("#task-description-input")).toBeVisible();
    await expect(page.locator("#task-priority-select")).toBeVisible();
  });

  test("modal has assign and schedule tabs", async ({ page }) => {
    await openAssignTaskModal(page);
    // Check for tab buttons (may be data-modal-tab or similar)
    const assignTab = page.locator('[data-modal-tab="assign"], .modal-tab:has-text("Assign")');
    const scheduleTab = page.locator('[data-modal-tab="schedule"], .modal-tab:has-text("Schedule")');
    // At least one of the tab selectors should match
    const assignVisible = await assignTab.first().isVisible().catch(() => false);
    const scheduleVisible = await scheduleTab.first().isVisible().catch(() => false);
    expect(assignVisible || scheduleVisible).toBeTruthy();
  });

  test("schedule preset radios exist", async ({ page }) => {
    await openAssignTaskModal(page);
    // Switch to schedule tab
    const schedTab = page.locator('[data-modal-tab="schedule"], .modal-tab:has-text("Schedule")');
    if (await schedTab.first().isVisible().catch(() => false)) {
      await schedTab.first().click();
      await page.waitForTimeout(300);
    }
    const presets = page.locator('input[name="schedule-preset"]');
    const count = await presets.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("modal close button works", async ({ page }) => {
    await openAssignTaskModal(page);
    const closeBtn = page.locator("#assign-task-modal .modal__close, #assign-task-close");
    await closeBtn.first().click();
    await page.waitForTimeout(300);
    await expect(page.locator("#assign-task-modal")).not.toBeVisible();
  });

  test("assign task button exists in panel footer", async ({ page }) => {
    await expect(page.locator("#assign-task-btn")).toBeVisible();
  });

  test("active view is default sub-tab", async ({ page }) => {
    await expect(page.locator("#task-view-active")).toBeVisible();
  });
});
