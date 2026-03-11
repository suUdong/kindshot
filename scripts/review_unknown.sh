#!/usr/bin/env bash
# Review today's UNKNOWN-bucket headlines for keyword candidates.
# Usage: bash scripts/review_unknown.sh [YYYY-MM-DD]

set -euo pipefail

DATE="${1:-$(TZ=Asia/Seoul date +%Y-%m-%d)}"
LOG_DIR="${LOG_DIR:-logs}"
FILE="$LOG_DIR/unknown_headlines/$DATE.jsonl"

if [ ! -f "$FILE" ]; then
    echo "No unknown headlines for $DATE"
    exit 0
fi

TOTAL=$(wc -l < "$FILE")
UNIQUE=$(sort -u "$FILE" | wc -l)

echo "=== UNKNOWN headlines for $DATE ==="
echo "Total: $TOTAL / Unique: $UNIQUE"
echo ""

# Show unique headlines sorted by frequency
jq -r '.headline' "$FILE" | sort | uniq -c | sort -rn | head -30
