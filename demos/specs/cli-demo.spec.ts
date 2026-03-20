import { test } from "@playwright/test";
import * as path from "path";
import { DemoTimer, snap } from "../helpers/demo-utils";

/**
 * CLI Demo: Terminal-style recording
 *
 * Opens a local HTML page styled as a terminal and replays
 * CLI commands with typing animations. Playwright records
 * the viewport as a .webm video.
 *
 * Output:
 *   - recordings/cli-demo.webm  (video via Playwright)
 *   - recordings/cli-timing.json (timing data)
 *
 * No server required -- this is a self-contained HTML page.
 */

const timer = new DemoTimer();

test.describe("CLI Terminal Demo", () => {
  test("record CLI walkthrough", async ({ page }) => {
    timer.start();

    // Load the terminal HTML page directly (file://)
    const terminalPath = path.resolve(
      __dirname,
      "../helpers/cli-terminal.html"
    );
    await page.goto(`file:///${terminalPath.replace(/\\/g, "/")}`);
    await page.waitForTimeout(500);
    timer.mark("page_loaded");

    // Wait for the demo script to finish
    // The HTML page sets data-demo-complete="true" when done
    await page.waitForSelector('body[data-demo-complete="true"]', {
      timeout: 120_000,
    });
    timer.mark("demo_complete");

    // Final screenshot
    await snap(page, "cli-final", 1);

    // Save timing
    timer.save("recordings/cli-timing.json");

    const summary = timer.summary();
    console.log("\n========================================");
    console.log("  CLI DEMO TIMING");
    console.log("========================================");
    console.log(summary);
    console.log("========================================\n");
  });
});
