#!/usr/bin/env bash
# file_collaboration.sh -- Two agents collaborating via a shared JSONL file.
# No server needed. Cohort acts as the referee.
#
# Usage:
#   bash examples/file_collaboration.sh

set -e

FILE="conversation.jsonl"
AGENTS="agents.json"
CHANNEL="design-review"

# Clean up from previous runs
rm -f "$FILE" "${FILE%.jsonl}_channels.json"

# Define agents -- any language can write this JSON
cat > "$AGENTS" << 'EOF'
{
  "architect": {
    "triggers": ["api", "design", "architecture"],
    "capabilities": ["backend architecture", "REST API design"]
  },
  "tester": {
    "triggers": ["testing", "qa", "validation"],
    "capabilities": ["test strategy", "integration testing"]
  }
}
EOF

echo "=== Cohort: file-based agent collaboration ==="
echo ""

# 1. Architect starts the conversation
echo "[1] architect posts an opening message..."
python -m cohort say --sender architect --channel "$CHANNEL" \
    --file "$FILE" \
    --message "Let's review the API design for the /users endpoint. I think we need pagination."
echo ""

# 2. Check if tester should respond
echo "[2] Should tester respond?"
if python -m cohort gate --agent tester --channel "$CHANNEL" \
    --file "$FILE" --agents "$AGENTS"; then
    echo "    -> tester is cleared to speak."
else
    echo "    -> tester should stay silent."
fi
echo ""

# 3. Who should speak next?
echo "[3] Next speaker recommendation:"
python -m cohort next-speaker --channel "$CHANNEL" \
    --file "$FILE" --agents "$AGENTS" --top 2
echo ""

# 4. Tester responds
echo "[4] tester posts a response..."
python -m cohort say --sender tester --channel "$CHANNEL" \
    --file "$FILE" \
    --message "Good call. We should also add rate limiting tests for the paginated endpoint."
echo ""

# 5. Check the updated speaker ranking
echo "[5] Updated speaker ranking:"
python -m cohort next-speaker --channel "$CHANNEL" \
    --file "$FILE" --agents "$AGENTS" --top 2
echo ""

# 6. Show the raw JSONL file
echo "=== Raw conversation file ==="
cat "$FILE"
echo ""

# Cleanup
rm -f "$FILE" "${FILE%.jsonl}_channels.json" "$AGENTS"
echo "[OK] Demo complete. All temp files cleaned up."
