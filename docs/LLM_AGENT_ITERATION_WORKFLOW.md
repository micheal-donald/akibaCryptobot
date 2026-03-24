# LLM Agent Iteration Workflow

## Purpose

This document defines the working protocol used for spec-driven, phased, AI-assisted development in this repository.

The workflow is designed for:

- phased refactors
- bounded agent execution
- main-thread review and control
- direct server deployment outside GitHub Actions when needed
- continuous visible progress reporting

It is written so another LLM can adopt the same operating mode with minimal extra instruction.

## Workflow Name

**Spec-Driven Phased Agent Iteration**

## Core Principle

One bounded slice moves at a time through a fixed loop:

1. define the phase or task in a spec
2. assign one bounded slice to a worker agent
3. let the worker run
4. review the worker output in the main thread
5. patch if needed
6. test
7. commit and push
8. deploy only if the slice changes runtime behavior
9. report status
10. start the next slice

The main thread owns correctness, repo state, deployment, and status reporting.
The worker owns bounded implementation only.

## Roles

### Main Thread

The main thread is responsible for:

- maintaining the phase plan
- writing and updating phase specs
- choosing the next bounded slice
- spawning or reusing the worker
- reviewing all worker output
- patching weak or incorrect work
- running tests and lint
- committing and pushing accepted work
- performing direct server deployment
- verifying deployed state
- reporting progress in-thread without waiting for prompts

### Worker Agent

The worker is responsible for:

- implementing one bounded slice only
- staying inside the assigned write scope
- not reverting unrelated changes
- running focused tests for touched files
- reporting changed files, behavior, and test results

### Deployment Control

Deployment is centralized in the main thread.

Worker agents do **not** deploy.
Worker agents do **not** decide whether a slice is accepted.

## Standing Operating Rules

1. Work in phases.
2. Each phase is broken into small named tasks such as `P4-03` or `P5-02`.
3. Always use a written spec for the current phase.
4. Prefer one active worker task at a time unless there is a clear benefit to safe parallelism.
5. Do not take over a worker task locally unless:
   - the worker has clearly failed, or
   - the user explicitly asks for takeover.
6. Keep the worker context stable by reusing the same worker when practical.
7. Keep deployment pinned to a reviewed SHA, never floating `HEAD`.
8. Do not redeploy test-only or doc-only slices.
9. Report progress at every checkpoint without waiting for the user to ask.
10. Treat review as a gate, not a suggestion.

## Iteration Loop

This is the exact loop to repeat:

1. Validate the previous worker task.
2. If acceptable, commit and push it.
3. If not acceptable, patch it in the main thread, then test, commit, and push.
4. Reuse or spawn the worker for the next bounded task.
5. Create or update the implementation spec for the next phase when needed.
6. Repeat without pausing for user confirmation unless there is a real blocker or risky decision.

## Checkpoint Reporting Rules

The main thread must report at these checkpoints:

- worker started
- worker finished
- worker output accepted or rejected
- local patch applied
- tests passed or failed
- commit pushed
- deployment started
- deployment verified
- next task started

If nothing has changed, report the actual current state instead of going silent.

## Task Design Rules

Every worker task must be:

- bounded
- additive where possible
- easy to review
- small enough to test quickly
- tied to one spec section

Each worker prompt should include:

- the phase task id
- the exact goal
- the write scope
- explicit non-goals
- instructions not to revert unrelated changes
- required tests to run
- required report format

## Review Standard

A worker slice is accepted only if:

- the code matches the phase spec
- the scope is correct
- tests pass
- lint passes on touched files
- no obvious correctness issue is visible

If a slice is weak but salvageable:

- patch locally
- rerun tests
- commit the corrected version

If a slice is out of scope or wrong in direction:

- reject it
- reissue the bounded task

## Test Policy

Run tests in layers:

1. focused tests for touched files
2. relevant regression suite
3. broader runtime suite when the change touches runtime contracts or services

Use lint on touched files before accepting a slice.

## Deployment Policy

Deploy only from the main thread.

Use direct server deployment when the workflow should not rely on GitHub Actions.

Deployment rules:

- deploy only a reviewed commit SHA
- verify the server SHA after deploy
- verify service state after deploy
- verify public health/status after deploy
- do not restart the server for doc-only or test-only changes

## Server Verification Checklist

After a runtime-affecting deploy, verify:

- deployed SHA matches the reviewed SHA
- service is `active`
- service is `enabled`
- public health endpoint is OK
- public status endpoint is OK
- trading mode matches expectation

## Phase Management

Each phase should have:

- one dedicated markdown spec
- a task breakdown
- acceptance criteria
- clear non-goals

Do not start implementation for a new phase without a written spec.

## Recommended File Artifacts

Use this structure:

- phase spec: `docs/specs/PHASE_N_*.md`
- reusable task template: `docs/specs/AI_TASK_SPEC_TEMPLATE.md`
- workflow definition: `docs/LLM_AGENT_ITERATION_WORKFLOW.md`

## Reusable Prompt Contract For Another LLM

Use the following operating prompt when applying this workflow elsewhere:

```text
Operate in a spec-driven phased workflow.

Rules:
- Work in bounded phase tasks.
- Use one worker task at a time unless parallelism is clearly safe.
- Let the worker run; do not take over locally unless the worker fails or the user asks.
- Review every worker slice in the main thread.
- Patch locally if needed.
- Run focused tests, then relevant regression tests.
- Commit and push only reviewed work.
- Deploy only reviewed runtime-affecting SHAs.
- Do not deploy doc-only or test-only changes.
- Report progress at every checkpoint without waiting for prompts.

Iteration loop:
1. Validate previous task.
2. Commit/push if acceptable.
3. Start next task with bounded scope.
4. Update the next-phase spec when needed.
5. Repeat.
```

## Example Status Report Format

Use a compact status report like this:

```text
Current status:
- Active phase: Phase 5
- Last accepted commit: e85407f
- Active task: P5-04
- Server SHA: 16b8ec39...
- Deploy state: unchanged, no runtime deploy needed for current slice

Next step:
- review worker output
- test
- push if accepted
- deploy only if runtime-affecting
```

## Stop Conditions

Pause the loop only if:

- the user changes direction
- the phase spec is missing and must be written first
- deployment risk is ambiguous
- the worker task is blocked on an unknown requirement
- the codebase has conflicting unreviewed changes that make the task unsafe

## Summary

This workflow is:

- phased
- spec-driven
- review-gated
- worker-assisted
- main-thread controlled
- deployment-safe
- status-visible

It is a good fit for long refactors where correctness, continuity, and operator control matter more than raw speed.
