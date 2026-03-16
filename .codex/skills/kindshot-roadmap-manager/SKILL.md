---
name: kindshot-roadmap-manager
description: Maintain roadmap-driven execution for Kindshot Codex improvement runs. Use when Codex needs to choose the next bounded task, align a self-improve run to the current phase, or update roadmap and run-summary state after completing work.
---

# Kindshot Roadmap Manager

## Workflow

1. Read `AGENTS.md`, `memory/codex-loop/roadmap.md`, `memory/codex-loop/session.md`, and `memory/codex-loop/latest.md` before proposing work.
2. If `memory/codex-loop/ops-backlog.md` exists, read it too and treat it as the source of truth for log-driven cleanup slices.
3. Treat `memory/codex-loop/roadmap.md` as the ordering source of truth for strategy/phase work unless the user explicitly overrides it.
4. Treat `memory/codex-loop/session.md` as the current handoff state for branch, blocker, and next-step context.
5. For any non-trivial feature, strategy, or pipeline behavior change, perform a design-first pass before implementation: analyze the current state, write/update the relevant design doc, and only then choose an implementation slice.
6. Choose one hypothesis that fits the active phase, keeps the diff reversible, and is large enough to deliver a user-visible or operator-visible capability rather than a helper-only micro-step.
7. Prefer work that improves data correctness, guardrails, observability, or automation discipline before adding new trading behavior.
8. After changes and validation, update `memory/codex-loop/latest.md`.
9. Update `memory/codex-loop/session.md` whenever branch, blocker, active hypothesis, or next step changed.
10. Update `memory/codex-loop/roadmap.md` only when phase status, current focus, or next-run candidates materially changed.
11. Update `memory/codex-loop/ops-backlog.md` when an operational cleanup item changes status, ordering, evidence, or mode assumptions.
12. Run implementation in batch mode by default: once a bounded slice is selected, carry it through design, code, validation, and memory updates without pausing for user confirmation unless a real blocker appears.

## Selection Rules

- If `ops-backlog.md` contains active items, prefer the first active `P0`, then `P1`, then `P2` item before roadmap candidate work.
- If the user explicitly requests batch mode, execute consecutive active items from `ops-backlog.md` using that file's batch rules; otherwise still keep feature work in single-slice batch mode.
- For feature work, prefer a larger bounded batch that completes one coherent user/operator-facing capability, including related writer/reader wiring, output shape, tests, and doc updates, instead of stopping after each small helper or log tweak.
- Do not stop a batch at intermediate plumbing states such as "index added", "helper added", "sink added", or "loader added" if the surrounding workflow still is not directly usable.
- Treat the desired batch size as "one workflow/CLI/report path that a user or operator can actually exercise end-to-end", not "one internal component change".
- If the requested work is a new feature or behavior change and no current design doc covers it in enough detail, stop at analysis/design for that run unless the user explicitly asks to continue into implementation after the design pass.
- Pick the first high-confidence candidate from the active phase unless recent evidence points to a clearer defect.
- If a task would span multiple hypotheses, split it by capability boundary, not helper boundary, and complete only the first reversible slice.
- If KIS API semantics are unclear, verify against the official example repository before editing code.
- Do not choose tasks that require `deploy/` edits, secret changes, or live-trading activation.
- Do not stop mid-slice for status confirmation unless blocked by missing access, permissions, high-risk ambiguity, or conflicting user changes.

## Output Rules

- Keep run summaries short and reviewable.
- Record hypothesis, changed files, validation result, and rollback note in `memory/codex-loop/latest.md`.
- Record current branch, blocker, and next intended step in `memory/codex-loop/session.md`.
- Keep roadmap entries phase-based, not changelog-shaped.
- When a run stops at design-first documentation, say so explicitly in the summary and record the implementation step as the next intended step.
- Keep `ops-backlog.md` ordered and operational; it is a queue, not a postmortem.
- When batch mode is used, summarize the completed batch items together and record the next untouched active item.
- For default implementation runs, prefer one concise end-of-batch report over frequent progress requests; only surface intermediate updates when work is long-running or a blocker appears.
- Default feature batches should usually include the main behavior change plus directly dependent polish needed to make that behavior usable and reviewable in one pass.
- When deciding whether to stop, bias toward continuing until the feature can be demonstrated as a coherent path rather than just described as newly possible.
- Prefer Python `3.11` validation; if the local environment is mismatched, use `uv run --python 3.11 --extra dev pytest -q`.
- When describing progress or proposing the next slice to the user, include a short progress snapshot in flat-list form so status is scannable at a glance.
- Use this exact shape when relevant:
  - `Done:` most recent completed slice from `session.md`
  - `Current:` active hypothesis for this run
  - `Next:` next intended step or the next 1-3 bounded slices from roadmap/review evidence
- Keep the snapshot brief and operational; do not turn it into a changelog.

## When Blocked

- If evidence is weak, update the run summary with findings and avoid code changes.
- If the next roadmap task depends on missing environment access or unstable external behavior, leave the phase unchanged and record the blocker in both `latest.md` and `session.md`.
