# Approach 1 vs Approach 2 — A/B comparison report

> **Status: TEMPLATE (awaiting runs).** The instrumentation (Phase 6) is
> in place; the numbers below are filled by running the pipeline 3× per
> variant on the same document set in an environment with GCP/Gemini
> access. See "How to run" below.

## What is being compared

| | Variant tag | Generation path |
|---|---|---|
| **Approach 1** | `multi_agent_manifest` | Extraction Manifest + `discovery_agent` + five section sub-agents (worker+formatter), validation with `manifest_prefilter` + `coverage` skill. |
| **Approach 2** | `root_skills_autoscoped` | Root loads one section skill at a time and generates inline (`save_<section>_bundle`), `AutoScopedSkillToolset` prunes inactive-skill context. No manifest, no discovery. Validation = deterministic + 4 semantic skills. |

The repair path (section `*_repair_agent` + `apply_<section>_patch`) is
**identical** in both variants, so the measured delta is the initial
generation path only.

## How to run (GCP/Gemini access required)

For each variant, 3 runs on the **same** fixed document set, same model
(`config.GEMINI_MODEL`), same `MAX_ROUNDS`, same thinking budget, no
extra optimisation (no prompt caching, no parallel sections):

1. Set the variant tag so telemetry buckets the run:
   - A2 (this branch): `export ARCHITECTURE_VARIANT=root_skills_autoscoped`
   - A1 (baseline branch): `export ARCHITECTURE_VARIANT=multi_agent_manifest`
2. Run the SOW generation end-to-end (e.g. `adk run app/` with the fixed
   3-document set: briefing + transcript + capability matrix).
3. At end of run, capture the metrics row:

   ```python
   from app.app_utils.run_metrics import collect_run_metrics
   from app.config import config
   row = collect_run_metrics(
       session.state,
       architecture_variant=config.ARCHITECTURE_VARIANT,
       model=config.GEMINI_MODEL,
       run_id="<run-id>",
   )
   ```

4. Fill the harness-only fields on `row` that the callbacks cannot see —
   they come back as `None` from `collect_run_metrics`:
   - `total_duration_s`, `generation_duration_s`, `validation_duration_s`,
     `quality_loop_duration_s` — wrap `Runner.run()` with timers.
   - `llm_call_count`, `estimated_input_tokens`, `estimated_output_tokens`
     — sum the Gemini SDK `usage_metadata` across calls.
   - `significant_findings_initial` — BLOCKER+MAJOR count from the FIRST
     critic round's report (the final-round count is auto-filled).
5. Average the 3 runs per variant; report mean ± stddev and Δ% vs A1.

## Metrics that are auto-collected from session state

`collect_run_metrics` fills these deterministically (no harness work):

`tool_call_count`, `load_artifacts_call_count`, `skills_loaded_count`,
`skill_resources_loaded_count`, `pruned_messages_count`,
`pruned_bytes_estimate`, `bundle_tool_payload_bytes`,
`section_generation_duration_by_section`, `quality_loop_rounds`,
`significant_findings_final`, `patch_ops_applied`, `patch_rejections`,
`anchor_drops`, `final_status`.

## Results

| Metric | A1 `multi_agent_manifest` (mean ± sd, n=3) | A2 `root_skills_autoscoped` (mean ± sd, n=3) | Δ |
|---|---|---|---|
| total_duration_s | … | … | … |
| generation_duration_s | … | … | … |
| validation_duration_s | … | … | … |
| quality_loop_duration_s | … | … | … |
| llm_call_count | … | … | … |
| tool_call_count | … | … | … |
| load_artifacts_call_count | … | … | … |
| skills_loaded_count | n/a | … | n/a |
| skill_resources_loaded_count | n/a | … | n/a |
| pruned_messages_count | n/a | … | n/a |
| pruned_bytes_estimate | n/a | … | n/a |
| bundle_tool_payload_bytes | n/a | … | n/a |
| estimated_input_tokens | … | … | … |
| estimated_output_tokens | … | … | … |
| significant_findings_initial | … | … | … |
| significant_findings_final | … | … | … |
| patch_ops_applied | … | … | … |
| patch_rejections | … | … | … |
| anchor_drops | … | … | … |
| final_status | … | … | … |

## Interpretation

> Fill after the runs. The experiment answers: *can the root agent
> accumulate sequential skills (with auto-scoped pruning) without
> becoming a context monolith?* — watch `pruned_*`, the token counts,
> and `significant_findings_*` (a drift in findings vs A1 would signal
> the root-skills path is losing reasoning quality).
