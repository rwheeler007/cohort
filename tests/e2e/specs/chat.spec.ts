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
 * Chat feature tests -- run when chat-related files change.
 *
 * Verifies message send/receive, agent responses, and chat rendering.
 *
 * Tag: @chat
 */

test.describe("@chat Message Flow", () => {
  const TEST_CHANNEL = "e2e-chat-test";
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

  test("send message appears in message list", async ({ page }) => {
    // Create a test channel
    await createChannel(page, TEST_CHANNEL);
    await switchToChannel(page, TEST_CHANNEL);

    // Send a message
    await sendMessage(page, "Hello from E2E test");

    // Verify it appeared
    const messages = page.locator("#messages-list .message");
    const lastMessage = messages.last();
    await expect(lastMessage).toContainText("Hello from E2E test", {
      timeout: 5_000,
    });
  });

  test("message with @mention triggers agent response", async ({ page }) => {
    test.setTimeout(120_000); // LLM inference can be slow on first request
    await createChannel(page, `${TEST_CHANNEL}-mention`);
    await switchToChannel(page, `${TEST_CHANNEL}-mention`);

    // Send a message with @mention
    await sendMessage(
      page,
      "@python_developer What is 2+2? Answer in one word."
    );

    // Wait for agent response (may take a while with local LLM)
    await waitForNewMessage(page, 90_000);

    // Verify an agent message appeared (not just our own)
    const messages = page.locator("#messages-list .message");
    const count = await messages.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test("message markdown renders correctly", async ({ page }) => {
    await createChannel(page, `${TEST_CHANNEL}-markdown`);
    await switchToChannel(page, `${TEST_CHANNEL}-markdown`);

    // Send a message with markdown code block
    await sendMessage(page, "Here is code: `console.log('test')`");

    // Check that inline code is rendered (not raw backticks)
    const messages = page.locator("#messages-list .message");
    const lastMessage = messages.last();

    // Should contain a <code> element, not raw backticks
    const codeElement = lastMessage.locator("code");
    await expect(codeElement.first()).toBeVisible({ timeout: 5_000 });
  });

  test("multiple messages maintain order", async ({ page }) => {
    await createChannel(page, `${TEST_CHANNEL}-order`);
    await switchToChannel(page, `${TEST_CHANNEL}-order`);

    // Send messages in sequence
    await sendMessage(page, "Message ONE");
    await page.waitForTimeout(500);
    await sendMessage(page, "Message TWO");
    await page.waitForTimeout(500);
    await sendMessage(page, "Message THREE");

    // Verify order
    const messages = page.locator("#messages-list .message");
    const allText = await messages.allTextContents();
    const fullText = allText.join(" ");

    // ONE should appear before TWO, TWO before THREE
    const oneIdx = fullText.indexOf("Message ONE");
    const twoIdx = fullText.indexOf("Message TWO");
    const threeIdx = fullText.indexOf("Message THREE");

    expect(oneIdx).toBeLessThan(twoIdx);
    expect(twoIdx).toBeLessThan(threeIdx);
  });
});
