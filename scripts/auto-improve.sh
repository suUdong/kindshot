#!/usr/bin/env bash
#
# auto-improve.sh — Autonomous improvement loop for Kindshot
#
# Reads IMPROVEMENT_ANALYSIS.md, picks the next unchecked item,
# dispatches to opendev for implementation, verifies with tests,
# commits on success, and moves to the next item.
#
# Usage:
#   ./scripts/auto-improve.sh              # Run one improvement cycle
#   ./scripts/auto-improve.sh --loop       # Run until all items done (or failure)
#   ./scripts/auto-improve.sh --loop 3     # Run up to 3 cycles
#   ./scripts/auto-improve.sh --dry-run    # Show next item without executing
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

ANALYSIS_FILE="IMPROVEMENT_ANALYSIS.md"
SUMMARY_DIR="memory/codex-loop"
SUMMARY_FILE="$SUMMARY_DIR/latest.md"
LOG_DIR="logs/auto-improve"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Defaults
LOOP_MODE=false
MAX_CYCLES=1
DRY_RUN=false
OPENDEV_CMD="${OPENDEV_CMD:-opendev}"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --loop)
            LOOP_MODE=true
            if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
                MAX_CYCLES="$2"
                shift
            else
                MAX_CYCLES=999
            fi
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Usage: $0 [--loop [N]] [--dry-run]"
            exit 1
            ;;
    esac
done

mkdir -p "$LOG_DIR" "$SUMMARY_DIR"

# Extract the next unchecked item from IMPROVEMENT_ANALYSIS.md
# Returns: section_title | task_description
extract_next_task() {
    local current_section=""
    local found=false

    while IFS= read -r line; do
        # Track section headers (### X.X Title)
        if [[ "$line" =~ ^###[[:space:]]+([0-9]+\.[0-9]+)[[:space:]]+(.*) ]]; then
            current_section="${BASH_REMATCH[1]} ${BASH_REMATCH[2]}"
        fi
        # Find first unchecked item
        if [[ "$line" =~ ^-[[:space:]]\[[[:space:]]\][[:space:]]+(.*) ]] && ! $found; then
            local task="${BASH_REMATCH[1]}"
            echo "${current_section}|${task}"
            found=true
            return 0
        fi
    done < "$ANALYSIS_FILE"

    return 1  # No more tasks
}

# Extract the full context for a section (everything between its ### header and the next ---)
extract_section_context() {
    local section_num="$1"  # e.g., "1.1"
    local in_section=false
    local context=""

    while IFS= read -r line; do
        if [[ "$line" =~ ^###[[:space:]]+${section_num}[[:space:]] ]]; then
            in_section=true
        elif $in_section && [[ "$line" == "---" ]]; then
            break
        fi
        if $in_section; then
            context+="$line"$'\n'
        fi
    done < "$ANALYSIS_FILE"

    echo "$context"
}

# Mark a task as completed in IMPROVEMENT_ANALYSIS.md
mark_task_done() {
    local task_text="$1"
    # Escape special regex characters in the task text
    local escaped
    escaped=$(printf '%s' "$task_text" | sed 's/[]\/$*.^[]/\\&/g')
    sed -i "0,/- \[ \] ${escaped}/s//- [x] ${escaped}/" "$ANALYSIS_FILE"
}

# Run a single improvement cycle
run_cycle() {
    local cycle_num="$1"

    echo -e "${CYAN}=== Improvement Cycle #${cycle_num} ===${NC}"
    echo ""

    # Extract next task
    local task_info
    if ! task_info=$(extract_next_task); then
        echo -e "${GREEN}All improvement items completed!${NC}"
        return 1
    fi

    local section_title="${task_info%%|*}"
    local task_desc="${task_info##*|}"
    local section_num="${section_title%% *}"

    echo -e "${YELLOW}Section:${NC} $section_title"
    echo -e "${YELLOW}Task:${NC}    $task_desc"
    echo ""

    if $DRY_RUN; then
        echo -e "${CYAN}[dry-run] Would execute this task. Exiting.${NC}"
        return 1
    fi

    # Get full section context for richer prompt
    local section_context
    section_context=$(extract_section_context "$section_num")

    # Build the prompt for opendev
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M')
    local log_file="$LOG_DIR/cycle-${cycle_num}-$(date '+%Y%m%d_%H%M%S').log"

    local prompt
    prompt=$(cat <<PROMPT
You are working on the Kindshot project. Execute this specific improvement task:

## Task
${task_desc}

## Context (from IMPROVEMENT_ANALYSIS.md)
${section_context}

## Rules (from AGENTS.md)
- Apply exactly ONE improvement per run
- Keep diffs small and reversible
- Add or update tests for behavior changes
- Run tests after edits
- Never edit files under deploy/
- Never modify secrets, .env, or credential handling

## Execution Steps
1. Read relevant source files to understand current state
2. Implement the specific improvement described above
3. Add/update tests as needed
4. Run: python -m pytest tests/ -x -q
5. If tests fail, fix until they pass
6. Write a brief summary of what you changed and why

Focus ONLY on: ${task_desc}
Do NOT tackle other items from the analysis.
PROMPT
)

    echo -e "${CYAN}Dispatching to opendev...${NC}"
    echo ""

    # Run opendev in non-interactive mode
    local exit_code=0
    $OPENDEV_CMD -p "$prompt" -d "$PROJECT_DIR" \
        --dangerously-skip-permissions \
        2>&1 | tee "$log_file" || exit_code=$?

    echo ""

    if [[ $exit_code -ne 0 ]]; then
        echo -e "${RED}opendev exited with code $exit_code${NC}"
        echo -e "${YELLOW}Log saved to: $log_file${NC}"
        return 1
    fi

    # Verify: run tests
    echo -e "${CYAN}Running verification tests...${NC}"
    local test_exit=0
    python -m pytest tests/ -x -q 2>&1 | tail -20 || test_exit=$?

    if [[ $test_exit -ne 0 ]]; then
        echo -e "${RED}Tests failed after improvement. Reverting...${NC}"
        git checkout -- . 2>/dev/null || true
        echo -e "${YELLOW}Reverted. Log saved to: $log_file${NC}"
        # Don't mark as done — will retry next cycle
        return 1
    fi

    echo -e "${GREEN}Tests passed!${NC}"

    # Check if there are actual changes
    if git diff --quiet && git diff --cached --quiet; then
        echo -e "${YELLOW}No changes made. Marking task as done anyway.${NC}"
        mark_task_done "$task_desc"
        return 0
    fi

    # Commit the changes
    local commit_msg="auto-improve: ${task_desc}

Section: ${section_title}
Cycle: #${cycle_num}
Timestamp: ${timestamp}

Automated improvement via auto-improve.sh + opendev"

    git add -A
    git commit -m "$commit_msg" || {
        echo -e "${RED}Commit failed${NC}"
        return 1
    }

    echo -e "${GREEN}Committed: ${task_desc}${NC}"

    # Mark the task as done in IMPROVEMENT_ANALYSIS.md
    mark_task_done "$task_desc"
    git add "$ANALYSIS_FILE"
    git commit -m "auto-improve: mark '${task_desc}' as done" || true

    # Update session summary
    cat > "$SUMMARY_FILE" <<EOF
# Auto-Improve Run Summary

- **Timestamp**: ${timestamp}
- **Cycle**: #${cycle_num}
- **Section**: ${section_title}
- **Task**: ${task_desc}
- **Status**: Completed
- **Tests**: Passed
- **Log**: ${log_file}

## Next
$(extract_next_task 2>/dev/null | sed 's/|/ — /' || echo "All items completed")
EOF

    echo ""
    echo -e "${GREEN}=== Cycle #${cycle_num} Complete ===${NC}"
    echo ""

    return 0
}

# Main
echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║   Kindshot Auto-Improve Loop             ║"
echo "║   Mode: $(if $LOOP_MODE; then echo "loop (max $MAX_CYCLES)"; else echo "single"; fi)$(printf '%*s' $((24 - ${#MAX_CYCLES})) '')║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

cycle=1
while [[ $cycle -le $MAX_CYCLES ]]; do
    if ! run_cycle $cycle; then
        if $LOOP_MODE && [[ $cycle -lt $MAX_CYCLES ]]; then
            echo -e "${YELLOW}Cycle failed. Stopping loop.${NC}"
        fi
        break
    fi

    if ! $LOOP_MODE; then
        break
    fi

    cycle=$((cycle + 1))

    # Brief pause between cycles
    if [[ $cycle -le $MAX_CYCLES ]]; then
        echo -e "${CYAN}Starting next cycle in 5s... (Ctrl+C to stop)${NC}"
        sleep 5
    fi
done

remaining=$(grep -c '^\- \[ \]' "$ANALYSIS_FILE" 2>/dev/null || echo "0")
completed=$(grep -c '^\- \[x\]' "$ANALYSIS_FILE" 2>/dev/null || echo "0")
echo ""
echo -e "${CYAN}Progress: ${completed} done / ${remaining} remaining${NC}"
