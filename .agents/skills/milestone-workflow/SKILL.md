---
name: milestone-workflow
description: >-
  Executes any Aurora AI Hotel Receptionist milestone (M1-M6) following the mandatory
  9-step mentoring protocol, strict milestone scoping, real test verification, and
  user gatekeeping. Use whenever starting, implementing, or reviewing a milestone.
---

# Aurora Milestone Execution & Mentoring Workflow

Use this runbook whenever working on Milestones `M1` through `M6` defined in
[00_ARCHITECTURE_AND_REQUIREMENTS.md](../../../docs/00_ARCHITECTURE_AND_REQUIREMENTS.md).

## Pre-Flight Checks

1. Confirm which milestone (`M1`–`M6`) the user explicitly approved.
2. Verify prior milestone exit gate was passed and explained by the user.
3. Read only the relevant sections of `00_ARCHITECTURE_AND_REQUIREMENTS.md` and existing code before editing.

## Mandatory 9-Step Execution Sequence

1. **Explain Customer Problem & Requirements**
   - State which front-desk Aurora workflows (`FR-01`..`FR-08`) and reliability targets (`NFR-01`..`NFR-06`) this milestone solves.
2. **Explain Architecture & Trade-offs**
   - Cover data flow, chosen libraries/patterns, and at least one rejected alternative (e.g., why `SELECT ... FOR UPDATE` beats optimistic-only retry under hot inventory contention).
3. **Define Acceptance Criteria**
   - List exact functional, edge-case, security, and test pass criteria before writing code.
4. **Implement ONLY the Current Milestone**
   - Build real, production-grade code strictly within the current milestone boundary.
   - Never add fake stubs or mock implementations to mimic future milestones.
   - Only introduce Redis or background workers if a concrete requirement in the active milestone demands it.
5. **Run Real Tests & Inspect Actual Outputs**
   - Run `pytest` and/or Alembic/Docker verification commands against real PostgreSQL.
   - Report the exact command run, pass/fail counts, and real output observed. Never hide failures or invent metrics.
6. **Explain Important Code Paths & Failure Cases**
   - Reference exact file paths and symbol names you wrote/inspected and walk through what fails on bad input, timeouts, or concurrency.
7. **Review Correctness & Security**
   - Check transaction boundaries, idempotency, RBAC/verification gates, PII protection, and `audit_logs`.
8. **Document Design Review Checks**
   - Summarize architecture decisions, failure modes, and verification checks for the milestone.
9. **Verify Exit Gate Before Advancing**
   - Confirm all acceptance criteria and integration tests pass cleanly before moving to the next milestone.
