import { Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

/** Cohort settings file that controls first-run detection.
 *  Priority: COHORT_DATA_DIR env var > g:/cohort/data (dev) > %LOCALAPPDATA%/Cohort/data */
function getSettingsPath(): string {
  // Check env var first
  if (process.env.COHORT_DATA_DIR) {
    return path.join(process.env.COHORT_DATA_DIR, "settings.json");
  }
  // Dev repo path (where `cohort serve` runs from g:/cohort)
  const devPath = path.resolve("g:/cohort/data/settings.json");
  if (fs.existsSync(devPath)) {
    return devPath;
  }
  // Standard install paths
  if (process.platform === "win32") {
    return path.join(
      process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
      "Cohort",
      "data",
      "settings.json"
    );
  }
  return path.join(os.homedir(), ".cohort", "data", "settings.json");
}

/** VS Code extension settings (controls extension wizard) */
function getVscodeSettingsPath(): string {
  return path.join(os.homedir(), ".claude", "cohort-vscode-settings.json");
}

// ---------------------------------------------------------------------------
// State reset — makes the server think it's a fresh install
// ---------------------------------------------------------------------------

export interface ResetOptions {
  /** Also reset VS Code extension settings (default: false) */
  vscode?: boolean;
  /** Custom data dir to clear channels from */
  dataDir?: string;
}

/**
 * Reset Cohort to first-run state.
 *
 * - Backs up settings.json and sets setup_completed=false so wizard triggers
 * - Clears demo channels (leaves real data alone)
 * - Optionally resets VS Code extension settings
 */
export async function resetToFirstRun(opts: ResetOptions = {}): Promise<void> {
  const settingsPath = getSettingsPath();

  // Back up existing settings, then flip setup_completed to false
  if (fs.existsSync(settingsPath)) {
    const backup = settingsPath + ".demo-backup";
    fs.copyFileSync(settingsPath, backup);

    // Modify in-place: set setup_completed = false
    const settings = JSON.parse(fs.readFileSync(settingsPath, "utf-8"));
    settings.setup_completed = false;
    fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
  }

  // Reset VS Code extension settings if requested
  if (opts.vscode) {
    const vsPath = getVscodeSettingsPath();
    if (fs.existsSync(vsPath)) {
      const backup = vsPath + ".demo-backup";
      fs.copyFileSync(vsPath, backup);
      const vsSettings = JSON.parse(fs.readFileSync(vsPath, "utf-8"));
      vsSettings.setup_completed = false;
      fs.writeFileSync(vsPath, JSON.stringify(vsSettings, null, 2));
    }
  }

  // Clear demo channels from data dir
  const dataDir =
    opts.dataDir ||
    path.dirname(settingsPath); // same dir as settings.json
  const channelsFile = path.join(dataDir, "channels.json");
  if (fs.existsSync(channelsFile)) {
    const channels = JSON.parse(
      fs.readFileSync(channelsFile, "utf-8")
    );
    // Only remove channels created by demos
    const filtered = Array.isArray(channels)
      ? channels.filter(
          (ch: any) => !ch.id?.startsWith("demo-")
        )
      : channels;
    fs.writeFileSync(channelsFile, JSON.stringify(filtered, null, 2));
  }
}

/**
 * Restore settings from backup after demo recording.
 */
export async function restoreFromBackup(): Promise<void> {
  for (const basePath of [getSettingsPath(), getVscodeSettingsPath()]) {
    const backup = basePath + ".demo-backup";
    if (fs.existsSync(backup)) {
      fs.copyFileSync(backup, basePath);
      fs.unlinkSync(backup);
    }
  }
}

// ---------------------------------------------------------------------------
// Human-like typing
// ---------------------------------------------------------------------------

export interface TypeOptions {
  /** Base delay between keystrokes in ms (default: 65) */
  delay?: number;
  /** Random jitter added to each keystroke in ms (default: 40) */
  jitter?: number;
  /** Pause before typing starts in ms (default: 300) */
  preDelay?: number;
}

/**
 * Type text with natural-looking timing variation.
 * Uses per-character delays with random jitter to look human.
 */
export async function typeHuman(
  page: Page,
  selector: string,
  text: string,
  opts: TypeOptions = {}
): Promise<void> {
  const { delay = 65, jitter = 40, preDelay = 300 } = opts;

  await page.waitForSelector(selector, { state: "visible" });
  await page.click(selector);
  await page.waitForTimeout(preDelay);

  for (const char of text) {
    const charDelay = delay + Math.random() * jitter;
    await page.keyboard.type(char, { delay: 0 });
    await page.waitForTimeout(charDelay);
  }
}

/**
 * Type into a contenteditable div (like #message-input).
 * Uses keyboard.type which works better with contenteditable than fill().
 */
export async function typeInContentEditable(
  page: Page,
  selector: string,
  text: string,
  opts: TypeOptions = {}
): Promise<void> {
  const { delay = 65, jitter = 40, preDelay = 300 } = opts;

  await page.waitForSelector(selector, { state: "visible" });
  await page.click(selector);
  await page.waitForTimeout(preDelay);

  // Clear any existing content
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(100);

  for (const char of text) {
    const charDelay = delay + Math.random() * jitter;
    await page.keyboard.type(char, { delay: 0 });
    await page.waitForTimeout(charDelay);
  }
}

// ---------------------------------------------------------------------------
// Screenshot + timing helpers
// ---------------------------------------------------------------------------

/**
 * Take a named screenshot with a timestamp prefix for ordering.
 */
export async function snap(
  page: Page,
  name: string,
  step: number
): Promise<void> {
  const padded = String(step).padStart(2, "0");
  await page.screenshot({
    path: `recordings/${padded}-${name}.png`,
    fullPage: false,
  });
}

/**
 * Simple stopwatch for measuring elapsed time in demos.
 */
export class DemoTimer {
  private startTime: number = 0;
  private marks: Array<{ name: string; elapsed: number }> = [];

  start(): void {
    this.startTime = Date.now();
    this.marks = [];
  }

  mark(name: string): number {
    const elapsed = Date.now() - this.startTime;
    this.marks.push({ name, elapsed });
    return elapsed;
  }

  elapsed(): number {
    return Date.now() - this.startTime;
  }

  /** Returns a summary string like "Total: 45.2s | wizard: 12.1s | channel: 3.4s | message: 8.7s" */
  summary(): string {
    const total = ((Date.now() - this.startTime) / 1000).toFixed(1);
    const parts = this.marks.map(
      (m) => `${m.name}: ${(m.elapsed / 1000).toFixed(1)}s`
    );
    return `Total: ${total}s | ${parts.join(" | ")}`;
  }

  /** Write timing results to a JSON file */
  save(filepath: string): void {
    const result = {
      total_ms: Date.now() - this.startTime,
      total_s: Number(((Date.now() - this.startTime) / 1000).toFixed(1)),
      marks: this.marks.map((m) => ({
        name: m.name,
        elapsed_ms: m.elapsed,
        elapsed_s: Number((m.elapsed / 1000).toFixed(1)),
      })),
      recorded_at: new Date().toISOString(),
    };
    fs.writeFileSync(filepath, JSON.stringify(result, null, 2));
  }
}

// ---------------------------------------------------------------------------
// Wait helpers
// ---------------------------------------------------------------------------

/**
 * Wait for a setup wizard step to become visible.
 */
export async function waitForSetupStep(
  page: Page,
  step: number
): Promise<void> {
  await page.waitForSelector(
    `.setup-wizard__step[data-step="${step}"]`,
    { state: "visible", timeout: 15_000 }
  );
  // Let animations settle
  await page.waitForTimeout(500);
}

/**
 * Wait for an agent response to appear in the chat.
 * Counts total .message divs in #messages-list — when a new one appears
 * after we sent ours, that's the agent's reply.
 */
export async function waitForAgentResponse(
  page: Page,
  timeoutMs: number = 60_000
): Promise<void> {
  // Count current messages (includes the one we just sent)
  const initialCount = await page.locator("#messages-list .message").count();

  await page.waitForFunction(
    (prevCount) => {
      const msgs = document.querySelectorAll("#messages-list .message");
      return msgs.length > prevCount;
    },
    initialCount,
    { timeout: timeoutMs }
  );

  // Let the response fully render (streaming may still be in progress)
  await page.waitForTimeout(2000);
}

/**
 * Wait for connection status to show "Connected".
 */
export async function waitForConnection(page: Page): Promise<void> {
  await page.waitForFunction(
    () => {
      const el = document.getElementById("connection-status");
      return el && el.textContent?.toLowerCase().includes("connect");
    },
    null,
    { timeout: 15_000 }
  );
  await page.waitForTimeout(500);
}
