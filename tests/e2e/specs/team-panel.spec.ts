/**
 * @team -- Team panel tests.
 *
 * Verifies the team panel renders, agent grid or empty state shows,
 * and sidebar elements exist.
 */

import { test, expect } from "@playwright/test";
import {
  ConsoleErrorCollector,
  skipSetupWizard,
  expectConnected,
  switchToPanel,
  expectPanelActive,
} from "../helpers/test-utils";

test.describe("@team Team Panel", () => {
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

  test("team panel is reachable via nav button", async ({ page }) => {
    await switchToPanel(page, "team");
    await expectPanelActive(page, "panel-team");
  });

  test("team nav button exists and is visible", async ({ page }) => {
    const teamBtn = page.locator('button[data-panel="team"]');
    await expect(teamBtn).toBeVisible();
  });

  test("team panel has expected structure", async ({ page }) => {
    await switchToPanel(page, "team");
    // Panel should exist and be visible
    const panel = page.locator("#panel-team");
    await expect(panel).toBeVisible({ timeout: 5_000 });
  });

  test("agent chat list exists in sidebar", async ({ page }) => {
    const agentList = page.locator("#agent-chat-list");
    await expect(agentList).toBeAttached();
  });

  test("channel list exists in sidebar", async ({ page }) => {
    const channelList = page.locator("#channel-list");
    await expect(channelList).toBeAttached();
  });
});
