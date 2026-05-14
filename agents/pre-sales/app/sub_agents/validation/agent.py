"""Compose the Validation Critic — single-pass SequentialAgent.

Pipeline (read top-down):

    deterministic_check_agent       — Python, structural checks
    manifest_prefilter_agent        — Python, priority tagging
    semantic_skills_parallel        — ParallelAgent of 5 LLM skills
    validation_aggregator_agent     — Python, dedupes + decides gate
    validation_summary_agent        — LlmAgent, text-only summary
    validation_assembler_agent      — Python, writes final report

Invariants enforced by the topology:
- LLM agents read state only via instruction providers; they never write
  gate fields (severity counts, overall_status, requires_human_review).
- The 5 semantic skills run concurrently and write to distinct state
  keys (`app:skill_findings:{name}`) — no race condition possible.
- Structural decisions are concentrated in `validation_aggregator_agent`.
- `validation_assembler_agent` is the single writer of
  ``state[STATE_VALIDATION_RESULT]`` and escalates control back to root.

Correction loop: the root agent owns the 4-round correction loop via
its prompt — when the critic returns ``blocked``, the root applies
edits using the ``sow-generator`` SkillToolset (already loaded at the
root) and re-transfers to ``validation_critic``. We intentionally do
not wrap the critic in a ``LoopAgent`` with a separate reviser sub-
agent: that would duplicate the SOW generation knowledge already
encoded in the ``sow-generator`` skill and inflate the per-call
context for no architectural gain.
"""

from __future__ import annotations
import os

from google.adk.agents import SequentialAgent
import google.auth

from .aggregator import validation_aggregator_agent
from .assembler import validation_assembler_agent
from .deterministic_check import deterministic_check_agent
from .manifest_prefilter import manifest_prefilter_agent
from .semantic_skills import semantic_skills_parallel
from .summary_agent import validation_summary_agent


_, project_id = google.auth.default()
os.environ['GOOGLE_CLOUD_PROJECT'] = project_id
os.environ['GOOGLE_CLOUD_LOCATION'] = 'global'
os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'True'


validation_critic = SequentialAgent(
    name='validation_critic',
    description=(
        'Validates the staged SOW: runs deterministic checks, five '
        'semantic skills in parallel, decides the gate in Python and '
        'writes the final ValidationReport to session state. Transfer '
        'to this agent only after staging the SOW in '
        '`state[app:sow:current]`.'
    ),
    sub_agents=[
        deterministic_check_agent,
        manifest_prefilter_agent,
        semantic_skills_parallel,
        validation_aggregator_agent,
        validation_summary_agent,
        validation_assembler_agent,
    ],
)
