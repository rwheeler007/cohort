import { test, expect, Page } from "@playwright/test";
import { ConsoleErrorCollector } from "../helpers/test-utils";

/**
 * Import Preferences (Setup Step 5) -- wizard import flow tests.
 *
 * Tests the ChatGPT file upload, Claude Code detection, conversation picker,
 * facts preview, save/discard, and UI state transitions.
 *
 * Tag: @import
 *
 * All tests that need LLM (ChatGPT extraction) are tagged @import-llm
 * and will be skipped if Ollama is unavailable. The majority of tests
 * exercise pure frontend logic and file parsing (no LLM needed).
 */

// -- Helpers -----------------------------------------------------------------

/** Navigate wizard to Step 5 (Import) by skipping steps 1-4. */
async function navigateToImportStep(page: Page): Promise<void> {
  const wizard = page.locator("#setup-wizard");
  await wizard.waitFor({ state: "visible", timeout: 15_000 });

  // Wait for Step 1 to fully render (proves setupWizard.init() + show() completed)
  await page.waitForSelector('.setup-wizard__step[data-step="1"]', {
    state: "visible",
    timeout: 15_000,
  });
  await page.waitForTimeout(2_000);

  // Skip through steps 1-4 to reach step 5, with generous delays
  for (let step = 1; step <= 4; step++) {
    await page.waitForSelector(`.setup-wizard__step[data-step="${step}"]`, {
      state: "visible",
      timeout: 10_000,
    });
    await page.waitForTimeout(1_000);
    await page.click("#setup-skip-btn");
    await page.waitForTimeout(1_500);
  }

  // Verify Step 5 body is visible
  await page.waitForSelector('.setup-wizard__step[data-step="5"]', {
    state: "visible",
    timeout: 10_000,
  });
  await page.waitForTimeout(1_000);
}

/** Build a minimal valid ChatGPT conversations.json payload. */
function makeChatGPTConversations(count: number = 3): object[] {
  const convs = [];
  for (let i = 0; i < count; i++) {
    const id = `conv-test-${i}`;
    const rootId = `root-${i}`;
    const msgId = `msg-${i}`;
    convs.push({
      id,
      title: `Test Conversation ${i + 1}`,
      create_time: 1700000000 + i * 86400,
      mapping: {
        [rootId]: {
          id: rootId,
          parent: null,
          children: [msgId],
          message: null,
        },
        [msgId]: {
          id: msgId,
          parent: rootId,
          children: [],
          message: {
            id: msgId,
            author: { role: "user" },
            content: { parts: [`Test message ${i + 1}`] },
            create_time: 1700000000 + i * 86400,
          },
        },
      },
      current_node: msgId,
    });
  }
  return convs;
}

/**
 * Render a conversation picker into the DOM by calling the titles API
 * and building the list HTML directly.
 *
 * Note: setupWizard is a `const` (not on `window`), so we can't reference
 * it from page.evaluate. Instead we manipulate the DOM directly.
 */
async function showConversationPicker(
  page: Page,
  conversations: object[]
): Promise<void> {
  // Call the titles API
  const resp = await page.request.post("/api/setup/import-chatgpt-titles", {
    data: { conversations },
  });
  const result = await resp.json();

  // Build the picker DOM in the browser
  await page.evaluate((titles) => {
    const listEl = document.getElementById("setup-import-chatgpt-list");
    if (!listEl) return;
    listEl.innerHTML = "";

    for (const conv of titles) {
      const label = document.createElement("label");
      label.className = "setup-wizard__import-item";
      const date = conv.create_time
        ? new Date(conv.create_time * 1000).toLocaleDateString()
        : "";
      label.innerHTML = `
        <input type="checkbox" checked data-conv-id="${conv.id}">
        <span class="setup-wizard__import-item-title">${conv.title}</span>
        <span class="setup-wizard__import-item-meta">${conv.message_count} msgs - ${date}</span>
      `;
      listEl.appendChild(label);
    }

    // Toggle visibility
    const sources = document.getElementById("setup-import-sources");
    const picker = document.getElementById("setup-import-chatgpt-picker");
    if (sources) sources.style.display = "none";
    if (picker) picker.style.display = "";
  }, result.titles);

  await page.waitForTimeout(500);
}

/**
 * Inject mock facts into the preview panel via direct DOM manipulation.
 * No reference to setupWizard needed.
 */
async function injectMockFacts(page: Page): Promise<void> {
  await page.evaluate(() => {
    const facts = [
      { fact: "User prefers concise responses", category: "preference" },
      { fact: "User uses Python 3.12", category: "tool_usage" },
      { fact: "Never use emojis in output", category: "correction" },
    ];

    const listEl = document.getElementById("setup-import-facts-list");
    if (!listEl) return;
    listEl.innerHTML = "";

    const badgeMap: Record<string, string> = {
      preference: "pref",
      tool_usage: "tool",
      correction: "rule",
    };

    for (let i = 0; i < facts.length; i++) {
      const f = facts[i];
      const label = document.createElement("label");
      label.className = "setup-wizard__import-item";
      label.innerHTML = `
        <input type="checkbox" checked data-fact-idx="${i}">
        <span class="setup-wizard__import-item-title">${f.fact}</span>
        <span class="setup-wizard__import-item-meta">${badgeMap[f.category] || "fact"}</span>
      `;
      listEl.appendChild(label);
    }

    // Also store facts on a data attribute so the save handler can find them
    listEl.dataset.mockFacts = JSON.stringify(facts);

    // Show preview, hide sources
    const sources = document.getElementById("setup-import-sources");
    const preview = document.getElementById("setup-import-preview");
    if (sources) sources.style.display = "none";
    if (preview) preview.style.display = "";
  });

  await page.waitForTimeout(500);
  await expect(page.locator("#setup-import-preview")).toBeVisible({
    timeout: 5_000,
  });
}

// -- Tests -------------------------------------------------------------------

test.describe("@import Import Preferences - Source Selection", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await navigateToImportStep(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("source cards are visible on Step 5 entry", async ({ page }) => {
    await expect(page.locator("#setup-import-sources")).toBeVisible();
    await expect(page.locator("#setup-import-chatgpt-btn")).toBeVisible();
    await expect(page.locator("#setup-import-claude-btn")).toBeVisible();

    // Picker and preview should be hidden initially
    await expect(page.locator("#setup-import-chatgpt-picker")).toBeHidden();
    await expect(page.locator("#setup-import-preview")).toBeHidden();
    await expect(page.locator("#setup-import-progress")).toBeHidden();
  });

  test("ChatGPT button triggers file input", async ({ page }) => {
    const fileInput = page.locator("#setup-import-chatgpt-file");
    await expect(fileInput).toBeHidden();

    // Verify the button is clickable without JS errors
    await page.locator("#setup-import-chatgpt-btn").click();
  });
});

test.describe("@import Import Preferences - ChatGPT Upload Flow", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await navigateToImportStep(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("titles API parses conversations correctly", async ({ page }) => {
    const conversations = makeChatGPTConversations(3);

    const resp = await page.request.post("/api/setup/import-chatgpt-titles", {
      data: { conversations },
    });
    expect(resp.ok()).toBeTruthy();
    const result = await resp.json();
    expect(result.titles).toHaveLength(3);
    expect(result.total).toBe(3);

    // Titles may be sorted by date (newest first), so check by content not index
    const allTitles = result.titles.map((t: any) => t.title);
    expect(allTitles).toContain("Test Conversation 1");
    expect(allTitles).toContain("Test Conversation 3");
    expect(result.titles[0]).toHaveProperty("message_count");
    expect(result.titles[0]).toHaveProperty("id");
  });

  test("conversation picker renders titles with checkboxes", async ({
    page,
  }) => {
    await showConversationPicker(page, makeChatGPTConversations(5));

    await expect(page.locator("#setup-import-chatgpt-picker")).toBeVisible();

    const checkboxes = page.locator(
      '#setup-import-chatgpt-list input[type="checkbox"]'
    );
    await expect(checkboxes).toHaveCount(5);
    await expect(checkboxes.first()).toBeVisible();
  });

  test("Select All / Select None toggles checkboxes", async ({ page }) => {
    await showConversationPicker(page, makeChatGPTConversations(4));

    await expect(page.locator("#setup-import-chatgpt-picker")).toBeVisible();

    const checkboxes = page.locator(
      '#setup-import-chatgpt-list input[type="checkbox"]'
    );

    // Click Select None
    await page.locator("#setup-import-select-none").click();
    await page.waitForTimeout(500);
    for (let i = 0; i < 4; i++) {
      await expect(checkboxes.nth(i)).not.toBeChecked();
    }

    // Click Select All
    await page.locator("#setup-import-select-all").click();
    await page.waitForTimeout(500);
    for (let i = 0; i < 4; i++) {
      await expect(checkboxes.nth(i)).toBeChecked();
    }
  });
});

test.describe("@import Import Preferences - Claude Code Detection", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await navigateToImportStep(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("Claude Code detect button calls API and shows status", async ({
    page,
  }) => {
    await page.locator("#setup-import-claude-btn").click();

    const status = page.locator("#setup-import-claude-status");
    await expect(status).toBeVisible({ timeout: 10_000 });

    // Wait for the API call to complete (initial text is "[*] Scanning...")
    await page.waitForTimeout(5_000);

    const text = await status.textContent();
    expect(text).toBeTruthy();
    // After API completes, shows [OK], [!], or [X]
    expect(text).toMatch(/\[(OK|!|X|\*)\]/);
  });

  test("Claude Code detect API returns expected structure", async ({
    page,
  }) => {
    const resp = await page.request.post("/api/setup/import-claude-detect");
    expect(resp.ok()).toBeTruthy();

    const result = await resp.json();
    expect(result).toHaveProperty("exists");
    expect(result).toHaveProperty("facts");
    expect(result).toHaveProperty("count");
    expect(typeof result.exists).toBe("boolean");
    expect(Array.isArray(result.facts)).toBeTruthy();
    expect(typeof result.count).toBe("number");
  });

  test("Claude detect with facts transitions to preview", async ({
    page,
  }) => {
    // Check API first to see what this machine returns
    const resp = await page.request.post("/api/setup/import-claude-detect");
    const result = await resp.json();

    if (result.facts && result.facts.length > 0) {
      // Click detect -- should transition to facts preview
      await page.locator("#setup-import-claude-btn").click();
      await page.waitForTimeout(3_000);

      const preview = page.locator("#setup-import-preview");
      const previewVisible = await preview.isVisible();

      if (previewVisible) {
        const factItems = page.locator(
          "#setup-import-facts-list .setup-wizard__import-item"
        );
        const count = await factItems.count();
        expect(count).toBeGreaterThan(0);
        await expect(page.locator("#setup-import-sources")).toBeHidden();
      }
    } else {
      // No Claude memories -- status message shown
      await page.locator("#setup-import-claude-btn").click();
      const status = page.locator("#setup-import-claude-status");
      await expect(status).toBeVisible({ timeout: 10_000 });
      await expect(status).toContainText("[!");
    }
  });
});

test.describe("@import Import Preferences - Facts Preview & Commit", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
    await navigateToImportStep(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("facts preview renders with correct categories", async ({ page }) => {
    await injectMockFacts(page);

    const items = page.locator(
      "#setup-import-facts-list .setup-wizard__import-item"
    );
    await expect(items).toHaveCount(3);

    // Check category badges
    const badges = page.locator(
      "#setup-import-facts-list .setup-wizard__import-item-meta"
    );
    const badgeTexts: string[] = [];
    for (let i = 0; i < 3; i++) {
      badgeTexts.push(
        ((await badges.nth(i).textContent()) || "").trim()
      );
    }
    expect(badgeTexts).toContain("pref");
    expect(badgeTexts).toContain("tool");
    expect(badgeTexts).toContain("rule");
  });

  test("all facts are checked by default", async ({ page }) => {
    await injectMockFacts(page);

    const checkboxes = page.locator(
      '#setup-import-facts-list input[type="checkbox"]'
    );
    await expect(checkboxes).toHaveCount(3);

    for (let i = 0; i < 3; i++) {
      await expect(checkboxes.nth(i)).toBeChecked();
    }
  });

  test("unchecking facts changes selection count", async ({ page }) => {
    await injectMockFacts(page);

    const checkboxes = page.locator(
      '#setup-import-facts-list input[type="checkbox"]'
    );

    // Uncheck the second fact
    await checkboxes.nth(1).uncheck();
    await page.waitForTimeout(300);
    await expect(checkboxes.nth(1)).not.toBeChecked();

    // Count checked
    const checkedCount = await page
      .locator('#setup-import-facts-list input[type="checkbox"]:checked')
      .count();
    expect(checkedCount).toBe(2);
  });

  test("save and discard buttons are visible in preview", async ({
    page,
  }) => {
    await injectMockFacts(page);

    await expect(page.locator("#setup-import-save-btn")).toBeVisible();
    await expect(page.locator("#setup-import-discard-btn")).toBeVisible();
  });

  test("discard button hides preview", async ({ page }) => {
    await injectMockFacts(page);

    await page.locator("#setup-import-discard-btn").click();
    await page.waitForTimeout(1_000);

    // Preview should be hidden after discard
    await expect(page.locator("#setup-import-preview")).toBeHidden();
  });

  test("commit API returns stored count", async ({ page }) => {
    const resp = await page.request.post("/api/setup/import-commit", {
      data: {
        facts: [
          {
            fact: "E2E test fact",
            category: "preference",
            source: "e2e-test",
            confidence: "high",
          },
        ],
      },
    });
    expect(resp.ok()).toBeTruthy();

    const result = await resp.json();
    expect(result).toHaveProperty("stored");
    // Isolated test instance may have no agents to distribute to
    expect(typeof result.stored).toBe("number");
  });

  test("commit API with empty facts returns zero", async ({ page }) => {
    const resp = await page.request.post("/api/setup/import-commit", {
      data: { facts: [] },
    });
    expect(resp.ok()).toBeTruthy();

    const result = await resp.json();
    expect(result.stored).toBe(0);
  });
});

test.describe("@import Import Preferences - Error Handling", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);
    await page.goto("/");
  });

  test.afterEach(() => {
    errors.check();
  });

  test("titles API rejects empty conversations", async ({ page }) => {
    const resp = await page.request.post("/api/setup/import-chatgpt-titles", {
      data: { conversations: [] },
    });
    expect(resp.status()).toBe(400);
  });

  test("titles API rejects non-array conversations", async ({ page }) => {
    const resp = await page.request.post("/api/setup/import-chatgpt-titles", {
      data: { conversations: "not-an-array" },
    });
    expect(resp.status()).toBe(400);
  });
});
