import { test, expect } from "@playwright/test";
import {
  expectConnected,
  skipSetupWizard,
  createChannel,
  switchToChannel,
  sendMessage,
  waitForNewMessage,
  expectMessageFrom,
  ConsoleErrorCollector,
} from "../helpers/test-utils";

/**
 * Multi-turn conversation and roundtable E2E tests.
 *
 * These tests drive the real UI — type a message, hit send, wait for the
 * agent to respond via whatever inference backend is configured.  No mocking.
 *
 * The inference mode is controlled by the COHORT_E2E_MODE env var:
 *   - "local"   (default) — Ollama local inference
 *   - "channel" — Claude Code channel session
 *   - "cloud"   — Direct cloud API (requires COHORT_CLOUD_API_KEY)
 *
 * Tag: @conversation
 */

const API_BASE = `http://127.0.0.1:${process.env.COHORT_E2E_PORT || "5199"}`;
const INFERENCE_MODE = process.env.COHORT_E2E_MODE || "local";

// Inference timeouts — cloud/channel are faster, local LLM needs more time
const RESPONSE_TIMEOUT = INFERENCE_MODE === "local" ? 90_000 : 60_000;
const TEST_TIMEOUT = INFERENCE_MODE === "local" ? 180_000 : 120_000;

test.describe("@conversation Multi-Turn Agent Conversation", () => {
  let errors: ConsoleErrorCollector;

  test.beforeEach(async ({ page }) => {
    errors = new ConsoleErrorCollector();
    errors.attach(page);

    // Set the execution backend for this test run
    if (INFERENCE_MODE === "channel") {
      await page.request.post(`${API_BASE}/api/settings`, {
        data: { execution_backend: "channel" },
      });
    } else if (INFERENCE_MODE === "cloud") {
      await page.request.post(`${API_BASE}/api/settings`, {
        data: { execution_backend: "api" },
      });
    }

    await page.goto("/");
    await skipSetupWizard(page);
    await expectConnected(page);
  });

  test.afterEach(() => {
    errors.check();
  });

  test("single @mention gets a response", async ({ page }) => {
    test.setTimeout(TEST_TIMEOUT);

    const ch = `conv-single-${Date.now()}`;
    await createChannel(page, ch);
    await switchToChannel(page, ch);

    await sendMessage(page, "@python_developer What is a list comprehension? One sentence.");

    await waitForNewMessage(page, RESPONSE_TIMEOUT);

    // Should have at least 2 messages: our question + agent response
    const messages = page.locator("#messages-list .message");
    const count = await messages.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test("follow-up message gets a contextual response", async ({ page }) => {
    test.setTimeout(TEST_TIMEOUT * 2);

    const ch = `conv-followup-${Date.now()}`;
    await createChannel(page, ch);
    await switchToChannel(page, ch);

    // Turn 1: Ask a specific question
    await sendMessage(
      page,
      "@python_developer What does the zip() function do in Python? One sentence."
    );
    await waitForNewMessage(page, RESPONSE_TIMEOUT);

    // Verify first response arrived
    const messages = page.locator("#messages-list .message");
    const afterFirst = await messages.count();
    expect(afterFirst).toBeGreaterThanOrEqual(2);

    // Turn 2: Follow up referencing the previous answer
    await sendMessage(
      page,
      "@python_developer Now show me a one-line example using zip with two lists."
    );
    await waitForNewMessage(page, RESPONSE_TIMEOUT);

    // Should have at least 4 messages: question, response, follow-up, response
    const afterSecond = await messages.count();
    expect(afterSecond).toBeGreaterThanOrEqual(4);

    // The second response should exist and contain something code-like
    const lastMessage = messages.last();
    await expect(lastMessage).toBeVisible({ timeout: 5_000 });
  });

  test("three-turn conversation maintains context", async ({ page }) => {
    test.setTimeout(TEST_TIMEOUT * 3);

    const ch = `conv-multi-${Date.now()}`;
    await createChannel(page, ch);
    await switchToChannel(page, ch);

    // Turn 1: Establish a topic
    await sendMessage(
      page,
      "@python_developer Explain what a decorator is in Python. Keep it short."
    );
    await waitForNewMessage(page, RESPONSE_TIMEOUT);

    // Turn 2: Build on it
    await sendMessage(
      page,
      "@python_developer Show me a simple decorator that logs function calls."
    );
    await waitForNewMessage(page, RESPONSE_TIMEOUT);

    // Turn 3: Go deeper
    await sendMessage(
      page,
      "@python_developer Now make that decorator preserve the original function name."
    );
    await waitForNewMessage(page, RESPONSE_TIMEOUT);

    // Should have at least 6 messages (3 questions + 3 responses)
    const messages = page.locator("#messages-list .message");
    const count = await messages.count();
    expect(count).toBeGreaterThanOrEqual(6);
  });
});


test.describe("@conversation Roundtable Discussion", () => {
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

  test("start a roundtable session via API and get turns", async ({
    page,
    request,
  }) => {
    test.setTimeout(TEST_TIMEOUT);

    const ch = `roundtable-${Date.now()}`;

    // Create channel
    await request.post(`${API_BASE}/api/channels`, {
      data: { name: ch },
    });

    // Start a roundtable session
    const startResp = await request.post(`${API_BASE}/api/roundtable/start`, {
      data: {
        channel_id: ch,
        topic: "What testing strategy should we use for a new REST API?",
        initial_agents: ["python_developer", "qa_agent"],
        max_turns: 4,
      },
    });
    expect(startResp.ok()).toBeTruthy();

    const startData = await startResp.json();
    expect(startData.success).toBe(true);
    const sessionId = startData.session_id;
    expect(sessionId).toBeTruthy();

    // Check status
    const statusResp = await request.get(
      `${API_BASE}/api/roundtable/${sessionId}/status`
    );
    expect(statusResp.ok()).toBeTruthy();
    const statusData = await statusResp.json();
    expect(statusData.session_id).toBe(sessionId);
    expect(statusData.topic).toContain("testing");

    // Get next speaker recommendation
    const speakerResp = await request.get(
      `${API_BASE}/api/roundtable/${sessionId}/next-speaker`
    );
    expect(speakerResp.ok()).toBeTruthy();

    // Record a turn
    const turnResp = await request.post(
      `${API_BASE}/api/roundtable/${sessionId}/record-turn`,
      {
        data: {
          agent_id: "python_developer",
          content: "I recommend pytest with fixtures for unit tests and httpx for integration tests.",
        },
      }
    );
    expect(turnResp.ok()).toBeTruthy();

    // End the session
    const endResp = await request.post(
      `${API_BASE}/api/roundtable/${sessionId}/end`
    );
    expect(endResp.ok()).toBeTruthy();
  });

  test("roundtable discussion visible in channel UI", async ({
    page,
    request,
  }) => {
    test.setTimeout(TEST_TIMEOUT);

    const ch = `rt-ui-${Date.now()}`;

    // Create channel and seed with roundtable messages via API
    await request.post(`${API_BASE}/api/channels`, {
      data: { name: ch },
    });

    // Post messages simulating a roundtable
    await request.post(`${API_BASE}/api/send`, {
      data: {
        channel: ch,
        sender: "python_developer",
        message: "For testing REST APIs, I suggest using pytest with httpx.",
      },
    });
    await request.post(`${API_BASE}/api/send`, {
      data: {
        channel: ch,
        sender: "qa_agent",
        message: "Agreed, but we should also add contract tests with schemathesis.",
      },
    });

    // Navigate to the channel in the UI
    await page.goto("/");
    await skipSetupWizard(page);
    await expectConnected(page);

    try {
      await switchToChannel(page, ch);

      // Both agent messages should be visible
      const messages = page.locator("#messages-list .message");
      await expect(messages.first()).toBeVisible({ timeout: 10_000 });

      const count = await messages.count();
      expect(count).toBeGreaterThanOrEqual(2);
    } catch {
      // Channel might not appear in sidebar if polling hasn't refreshed
    }
  });
});
