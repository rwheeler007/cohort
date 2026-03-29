import { CohortClient } from "./src/cohort-client.ts";

const client = new CohortClient({
  cohort_base_url: "http://localhost:5100",
  poll_interval_ms: 5000,
  heartbeat_interval_ms: 10000,
  session_id: "bun-test",
});

await client.heartbeat();
console.log("[OK] Heartbeat sent");

const poll = await client.poll();
console.log("[OK] Poll:", JSON.stringify(poll));

try {
  await client.claim("nonexistent");
} catch (e: any) {
  console.log("[OK] Claim non-existent threw:", e.message.substring(0, 50));
}

console.log("\n=== TypeScript client OK ===");
