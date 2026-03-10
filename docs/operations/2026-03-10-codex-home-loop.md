# Codex Home Loop (PC Engine + Android Control)

Date: 2026-03-10

## 1. Purpose

Set up a safe improvement loop for this repository:

- Home PC runs Codex automation on a self-hosted GitHub runner.
- Android phone monitors status and approves PRs in GitHub Mobile.
- Rate-limit/quota spikes pause automatically and retry later.

## 2. Added Components

- `AGENTS.md`
  - Safety and scope rules for automated edits.
- `.codex/prompts/self_improve.md`
  - Reusable prompt for one-hypothesis-per-run updates.
- `scripts/codex_exec_with_backoff.py`
  - Wrapper around `codex exec` with exponential backoff on quota/rate-limit errors.
- `.github/workflows/codex-self-improve.yml`
  - Scheduled + manual loop on self-hosted runner.

## 3. One-Time Setup (Home PC)

1. Install dependencies:
   - Python 3.11+
   - Codex CLI (`codex --version` must work)
2. Register GitHub self-hosted runner on this repo with labels:
   - `self-hosted`
   - `codex-home`
3. Add repository secret:
   - `OPENAI_API_KEY`
4. Keep runner online during desired automation windows.

## 4. Triggering

- Scheduled run: every 2 hours (`cron: 0 */2 * * *`)
- Manual run: GitHub Actions -> `codex-self-improve` -> `Run workflow`
  - Optional inputs:
    - `run_reason`
    - `max_attempts`

## 5. Rate-Limit Behavior

When Codex returns quota/rate-limit failure strings (`429`, `rate limit`, `insufficient_quota`, etc.):

1. Wrapper waits using exponential backoff.
2. Default delays:
   - base: 900 sec (15 min)
   - max: 14400 sec (4 h)
   - max attempts: 6
3. If still blocked after max attempts, run fails and is visible in Actions.

Tune via workflow env:

- `CODEX_MAX_ATTEMPTS`
- `CODEX_BASE_DELAY_SEC`
- `CODEX_MAX_DELAY_SEC`

## 6. Safety Notes

- Workflow uses `workspace-write` sandbox and `approval-policy=never` for unattended execution.
- Automated prompt explicitly forbids:
  - edits under `deploy/`
  - secret or `.env` modifications
  - enabling live-trading behavior
- PR review remains human-controlled on phone before merge.

## 7. Android Operations

Use GitHub Mobile for:

- workflow failure/success checks
- PR review and merge decisions
- re-running workflow_dispatch manually

Use your secure remote access channel (for example Tailscale) only for emergency runner checks.
