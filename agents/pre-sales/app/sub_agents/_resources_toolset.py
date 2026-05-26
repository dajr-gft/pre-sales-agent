"""SkillToolset variant for section sub-agents.

The default ``google.adk.tools.skill_toolset.SkillToolset`` injects a
system instruction telling the model to use ``load_skill`` when a skill
seems relevant. Inside a section sub-agent that instruction is
redundant and counter-productive: the sub-agent is already operating
under its skill (the SKILL.md content is baked into ``instruction=``),
so it should not be told to "activate" itself or load its own SKILL.md
as a second copy in the conversation history.

``SectionResourcesToolset`` exposes a curated subset of the parent's
tools — only ``load_skill_resource`` and ``run_skill_script`` — and
suppresses the default instruction injection. Activation tools
(``list_skills``, ``load_skill``) are filtered out so a section
sub-agent cannot reload its own skill as a tool call, which would
reproduce the monolithic-context problem the decomposition was meant
to fix.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.skill_toolset import SkillToolset


# Names of the parent SkillToolset tools that section sub-agents are
# allowed to call. Everything else (list_skills, load_skill) is
# intentionally filtered out — keep this list explicit so the allowlist
# does not silently grow when the parent class gains new tools.
_ALLOWED_TOOL_NAMES: frozenset[str] = frozenset({
    'load_skill_resource',
    'run_skill_script',
})


class SectionResourcesToolset(SkillToolset):
    """``SkillToolset`` exposing only resource-access tools.

    Use one of these per section sub-agent, registering only the skill
    whose SKILL.md is already in the sub-agent's ``instruction=``, plus
    any reference-only skills the sub-agent needs read access to (e.g.
    ``sow-shared``).
    """

    async def process_llm_request(
        self, *, tool_context: Any, llm_request: Any
    ) -> None:
        # Intentionally no-op: the SKILL.md is already in the sub-agent's
        # instruction. Injecting the default "use load_skill" prompt would
        # duplicate that guidance and encourage the sub-agent to reload
        # its own skill, defeating the isolation we want.
        return

    async def get_tools(
        self, readonly_context: ReadonlyContext | None = None
    ) -> list[BaseTool]:
        tools = await super().get_tools(readonly_context)
        return [t for t in tools if t.name in _ALLOWED_TOOL_NAMES]
