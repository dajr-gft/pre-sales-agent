# Structural Audit Rules — tool-enforced (silent)

The `generate_architecture_diagram` tool runs a deterministic structural
audit against the spec before rendering. The audit is mechanical and
invisible — you do not emit an audit block, list, or JSON anywhere in the
conversation.

## Required arguments

The tool requires four arguments that together form the audit surface:

1. `nodes` — diagram nodes from sub-step (1d).
2. `edges` — diagram edges from sub-step (1d).
3. `architecture_description` — the text from sub-step (1b).
4. `technology_stack` — the table from sub-step (1c).

Pass all four on every call — the audit cross-checks them against each
other.

## Tool behavior

- If all BLOCKER checks pass, the tool returns success and the diagram is
  rendered.
- If any BLOCKER check fails, the tool returns a `ToolError` listing the
  defects. You then:
  1. Silently revise the offending artifact: (1b) description, (1c)
     technology stack, or (1d) diagram spec.
  2. Call `generate_architecture_diagram` again with the corrected arguments.
  3. Do not mention the audit, the failures, or the retry to the user.
- WARNING failures are logged but do not block. You do not need to react to
  them during the conversation.
- **Maximum 3 consecutive retries.** If the tool still fails after the third
  attempt, describe the remaining defects to the user in the conversation
  language and ask how to proceed.
- If diagram generation still fails after the allowed retries, do NOT skip
  the architecture review. Continue to present the textual sections only;
  the runtime gate downstream still requires explicit user approval before
  the document phase unlocks.

## What the audit checks

The audit enforces rules from `references/diagram-spec.md` mechanically. You
do not need to mentally evaluate each check — focus on producing a
high-quality description, table, and spec that naturally satisfy those
rules. In particular:

- Node labels must be functional and project-specific
  (`references/diagram-spec.md` → Part D — Node Labeling Rules).
- IAM must never appear as a diagram node
  (`references/diagram-spec.md` → Part B — Node Granularity Rules).
- Every edge must have a protocol/data label
  (`references/diagram-spec.md` → Part C — Edge Hygiene).
- Every node's `service` must be compatible with its `parent_cluster`
  (`references/diagram-spec.md` → Part A — Cluster Model).
- Every GCP service mentioned in the description must also appear in the
  Technology Stack table and as a diagram node
  (`references/tech-stack-table-rules.md` → consistency rules).
- At minimum one Entry Point, one Compute, and one Data node must be present
  (`references/diagram-spec.md` → Part G — Minimum Component Checklist).

## What NOT to do

- Never write `<architecture_audit>` in any output.
- Never list check IDs or statuses in the conversation.
- Never say "All checks passed" or "Running self-audit."
- If the user asks how the architecture is validated, describe the process
  conceptually in prose. Do not reproduce any checklist.
