"""Lint enforcing the obligatory reference-adherence pattern for SOW skills.

Implements Section 5.9 of the decomposition plan. The pattern has four
mechanisms each ``SKILL.md`` must adopt so the LLM cannot reinterpret
"concise" or "brief" as permission to shorten SOW content, and so any new
skill stays grounded in the loaded references rather than improvising:

    1. Priority block ``## Reference authority and depth rules`` with the
       phrase ``binding quality contract`` and a ``**Brevity scope rule:**``
       paragraph.
    2. Pre-step ``**Pre-step — Load and apply references`` before each
       content-producing step.
    3. Reference Compliance gate (``Reference Compliance`` sub-step or a
       ``Self-test checklist`` block) before exiting a content-producing
       step.
    4. Inline citations: every ``references/<file>.md`` mention lives
       inside backticks so the LLM treats it as a path, not prose.

Plus three skill-specific extras:

    7. ``sow-orchestrator`` has an explicit anti-batch-load rule and the
       Phase Step ordering (A through E).
    8. Non-orchestrator skills do not refer to other skills with
       "invoke skill X" / "call skill Y" — only ``load_skill`` /
       ``load_skill_resource`` semantics.
    9. ``sow-shared`` description says it must NOT be activated as a
       workflow skill.

The lint runs at collection time over ``app/skills/*/SKILL.md`` and
excludes:

- ``sow-generator`` — slated for deletion at the end of the migration.
- ``sow-discovery`` — out of scope for the decomposition; it predates
  the pattern.

Until the new skills exist, the parametrized tests produce zero cases and
pass trivially. They activate automatically as each new skill is added.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_SKILLS_DIR = Path(__file__).resolve().parents[2] / 'app' / 'skills'
EXCLUDED_SKILLS = {'sow-generator', 'sow-discovery'}

# Skills that host references only and have no workflow.
LIBRARY_SKILLS = {'sow-shared'}

# Skills that adopt all four mechanisms.
WORKFLOW_SKILLS_TAG = 'workflow'
# Skills whose Mechanism 2 (Pre-step) is satisfied via the orchestrator
# loading sow-shared on their behalf, not via owning references that must
# be loaded inline. The orchestrator itself loads section skills.
ORCHESTRATOR_SKILL = 'sow-orchestrator'


def _discover_skill_dirs() -> list[Path]:
    """Return SKILL.md-bearing directories under app/skills/, sans exclusions."""
    if not _SKILLS_DIR.exists():
        return []
    return sorted(
        p
        for p in _SKILLS_DIR.iterdir()
        if p.is_dir()
        and (p / 'SKILL.md').exists()
        and p.name not in EXCLUDED_SKILLS
    )


def _skill_ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


_ALL_SKILLS = _discover_skill_dirs()
_WORKFLOW_SKILLS = [p for p in _ALL_SKILLS if p.name not in LIBRARY_SKILLS]
_LIBRARY_SKILL_PATHS = [p for p in _ALL_SKILLS if p.name in LIBRARY_SKILLS]
_ORCHESTRATOR_PATHS = [p for p in _ALL_SKILLS if p.name == ORCHESTRATOR_SKILL]
_NON_ORCHESTRATOR_WORKFLOW = [
    p for p in _WORKFLOW_SKILLS if p.name != ORCHESTRATOR_SKILL
]


def _read(skill_path: Path) -> str:
    return (skill_path / 'SKILL.md').read_text(encoding='utf-8')


def _frontmatter(content: str) -> str:
    """Return the YAML frontmatter block (without delimiters) or empty str."""
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, flags=re.DOTALL)
    return match.group(1) if match else ''


# ---------------------------------------------------------------------------
# Mechanism 1 — Priority block
# ---------------------------------------------------------------------------


_AUTHORITY_HEADING_RE = re.compile(
    r'^##\s+Reference authority and depth rules\s*$', re.MULTILINE
)
_BINDING_PHRASE_RE = re.compile(r'binding quality contract', re.IGNORECASE)
_BREVITY_RULE_RE = re.compile(r'\*\*Brevity scope rule:\*\*')


@pytest.mark.parametrize(
    'skill_path', _WORKFLOW_SKILLS, ids=_skill_ids(_WORKFLOW_SKILLS)
)
def test_has_reference_authority_section(skill_path: Path) -> None:
    """Mechanism 1.a — priority block heading must exist."""
    content = _read(skill_path)
    assert _AUTHORITY_HEADING_RE.search(content), (
        f'{skill_path.name}/SKILL.md is missing the '
        '"## Reference authority and depth rules" section required by '
        'Mechanism 1 of Section 5.9. Without it, LLMs treat the loaded '
        'references as optional inspiration instead of a binding contract.'
    )


@pytest.mark.parametrize(
    'skill_path', _WORKFLOW_SKILLS, ids=_skill_ids(_WORKFLOW_SKILLS)
)
def test_authority_section_uses_binding_contract_phrase(
    skill_path: Path,
) -> None:
    """Mechanism 1.b — phrase 'binding quality contract' anchors the block."""
    content = _read(skill_path)
    assert _BINDING_PHRASE_RE.search(content), (
        f'{skill_path.name}/SKILL.md must contain the exact phrase '
        '"binding quality contract" in its reference-authority section. '
        'Soft synonyms ("important", "preferred") consistently fail to '
        'override the LLM brevity bias.'
    )


@pytest.mark.parametrize(
    'skill_path', _WORKFLOW_SKILLS, ids=_skill_ids(_WORKFLOW_SKILLS)
)
def test_authority_section_has_brevity_scope_rule(skill_path: Path) -> None:
    """Mechanism 1.c — brevity carve-out so terse style does not bleed into SOW content."""
    content = _read(skill_path)
    assert _BREVITY_RULE_RE.search(content), (
        f'{skill_path.name}/SKILL.md must include the literal '
        '"**Brevity scope rule:**" block so the LLM does not interpret '
        '"concise" instructions for conversational orchestration as '
        'permission to shorten SOW content.'
    )


# ---------------------------------------------------------------------------
# Mechanism 2 — Pre-step gate
# ---------------------------------------------------------------------------


_PRE_STEP_RE = re.compile(
    r'\*\*Pre-step\s+—\s+Load and apply references', re.IGNORECASE
)
# Em dash variants — em dash (—), hyphen, en dash — are all tolerated to
# avoid breaking the lint over copy-paste artifacts that the IDE silently
# normalizes.
_PRE_STEP_RE_FALLBACK = re.compile(
    r'\*\*Pre-step\s*[-–—]\s*Load and apply references',
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    'skill_path', _WORKFLOW_SKILLS, ids=_skill_ids(_WORKFLOW_SKILLS)
)
def test_has_pre_step_load_block(skill_path: Path) -> None:
    """Mechanism 2 — at least one Pre-step load block in every workflow skill."""
    content = _read(skill_path)
    assert _PRE_STEP_RE.search(content) or _PRE_STEP_RE_FALLBACK.search(
        content
    ), (
        f'{skill_path.name}/SKILL.md must contain at least one '
        '"**Pre-step — Load and apply references" block. Without it, '
        'the model proceeds to drafting/patching without re-reading the '
        'binding references and Mechanism 1 becomes decorative.'
    )


# ---------------------------------------------------------------------------
# Mechanism 3 — Reference Compliance gate
# ---------------------------------------------------------------------------


_COMPLIANCE_GATE_RE = re.compile(
    r'(Reference Compliance|Self-test checklist)', re.IGNORECASE
)


@pytest.mark.parametrize(
    'skill_path', _WORKFLOW_SKILLS, ids=_skill_ids(_WORKFLOW_SKILLS)
)
def test_has_reference_compliance_gate(skill_path: Path) -> None:
    """Mechanism 3 — a compliance gate exists before exiting content steps."""
    content = _read(skill_path)
    assert _COMPLIANCE_GATE_RE.search(content), (
        f'{skill_path.name}/SKILL.md must include a "Reference Compliance" '
        'sub-step or a "Self-test checklist" before the step exits. Without '
        'it the LLM may "forget" the references mid-generation.'
    )


# ---------------------------------------------------------------------------
# Mechanism 4 — Reference paths live inside backticks
# ---------------------------------------------------------------------------


# Match ``references/<file>.md`` OR ``<skill>/references/<file>.md`` in prose.
# The negative lookbehind avoids matches already wrapped by a backtick — those
# are the well-formed citations.
_REFERENCE_PATH_RE = re.compile(
    r'(?<!`)(?<!\w)([a-z][\w-]+/)?references/[\w./-]+\.md(?!`)'
)
# Lines that are obviously code blocks (start with four spaces or live inside
# a fenced block) are skipped — we lint prose, not code samples. Inline code
# spans (single backticks) are also stripped because the wrapping backticks
# already make those paths well-formed citations.
_FENCED_BLOCK_RE = re.compile(r'```')
_INLINE_CODE_RE = re.compile(r'`[^`\n]+`')


def _strip_fenced_blocks(content: str) -> str:
    """Remove fenced blocks and inline code spans so their paths are not flagged.

    The check is concerned with paths that appear in raw prose without any
    backtick wrapper — those are the ones the LLM will read as soft
    suggestions. Anything already inside a backtick (fenced block or inline
    span) is by definition a well-formed citation and must not trigger the
    lint.
    """
    parts = _FENCED_BLOCK_RE.split(content)
    # Keep the even-indexed parts (outside fences) — odd indices are the
    # fenced bodies including the opening line.
    without_fences = '\n'.join(parts[::2])
    # Strip remaining inline `...` spans (single-line, single-pair backticks).
    return _INLINE_CODE_RE.sub('', without_fences)


@pytest.mark.parametrize(
    'skill_path', _WORKFLOW_SKILLS, ids=_skill_ids(_WORKFLOW_SKILLS)
)
def test_reference_paths_are_in_backticks(skill_path: Path) -> None:
    """Mechanism 4 — every ``references/<file>.md`` is a backticked citation.

    Prose-only mentions are easy for the LLM to read as soft suggestions.
    Backticked paths feel like resource identifiers and the model is much
    more likely to treat them as ``load_skill_resource`` targets.
    """
    content = _strip_fenced_blocks(_read(skill_path))
    bad = _REFERENCE_PATH_RE.findall(content)
    assert not bad, (
        f'{skill_path.name}/SKILL.md mentions reference paths outside '
        f'backticks: {bad!r}. Wrap each path in backticks so the LLM '
        'parses it as a resource locator, not as prose.'
    )


# ---------------------------------------------------------------------------
# Item 7 — sow-orchestrator anti-batch-load rule + Phase Step ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'skill_path',
    _ORCHESTRATOR_PATHS,
    ids=_skill_ids(_ORCHESTRATOR_PATHS),
)
def test_orchestrator_forbids_batch_loading_section_skills(
    skill_path: Path,
) -> None:
    """Orchestrator must explicitly forbid loading section skills in batch.

    Without this rule, the LLM will "save time" by loading all section
    skills in the same turn, recreating the monolithic context the
    decomposition is meant to eliminate.
    """
    content = _read(skill_path)
    has_one_at_a_time = re.search(
        r'(one\s+at\s+a\s+time|one\s+section\s+skill\s+per)',
        content,
        re.IGNORECASE,
    )
    has_no_batch = re.search(
        r'(do\s+not\s+batch[- ]load|never\s+batch[- ]load|no\s+batch[- ]load)',
        content,
        re.IGNORECASE,
    )
    assert has_one_at_a_time and has_no_batch, (
        f'{skill_path.name}/SKILL.md must include both an "one at a time" '
        'instruction and an explicit "do not batch-load" prohibition for '
        'section skills (Section 5.9 item 7).'
    )


@pytest.mark.parametrize(
    'skill_path',
    _ORCHESTRATOR_PATHS,
    ids=_skill_ids(_ORCHESTRATOR_PATHS),
)
def test_orchestrator_documents_phase_step_ordering(skill_path: Path) -> None:
    """Orchestrator must list Phase Steps A through E explicitly."""
    content = _read(skill_path)
    expected = ('Phase Step A', 'Phase Step B', 'Phase Step C', 'Phase Step D', 'Phase Step E')
    missing = [label for label in expected if label not in content]
    assert not missing, (
        f'{skill_path.name}/SKILL.md is missing Phase Step labels: '
        f'{missing}. Section 5.9 item 7 requires the ordering A through E '
        'to be explicit in the orchestrator workflow.'
    )


# ---------------------------------------------------------------------------
# Item 8 — Non-orchestrator skills avoid "invoke skill" / "call skill" language
# ---------------------------------------------------------------------------


# Match "invoke <skill-name>" / "call <skill-name>" / "invoke the <skill-name> skill"
# but NOT "invoke `confirm_phase_completion`" (a tool, not a skill).
_FORBIDDEN_SKILL_VERBS_RE = re.compile(
    r'\b(?:invoke|call)\s+(?:the\s+)?(?:`?sow-[\w-]+`?|skill\s+\w+|\w+\s+skill)\b',
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    'skill_path',
    _NON_ORCHESTRATOR_WORKFLOW,
    ids=_skill_ids(_NON_ORCHESTRATOR_WORKFLOW),
)
def test_no_invoke_call_skill_phrasing(skill_path: Path) -> None:
    """Section 5.9 item 8 — skills are instruction packs, not services.

    Wording like "invoke sow-requirements" or "call the architecture skill"
    misleads the LLM into expecting executable services with structured
    outputs. The correct phrasing is "load skill X via load_skill / load_skill_resource
    and follow its instructions".
    """
    content = _read(skill_path)
    matches = _FORBIDDEN_SKILL_VERBS_RE.findall(content)
    assert not matches, (
        f'{skill_path.name}/SKILL.md contains skill-as-service phrasing: '
        f'{matches!r}. Replace with "load skill X (via load_skill / '
        'load_skill_resource) and follow its instructions".'
    )


# ---------------------------------------------------------------------------
# Item 9 — sow-shared frontmatter signals "do NOT activate as a workflow skill"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'skill_path',
    _LIBRARY_SKILL_PATHS,
    ids=_skill_ids(_LIBRARY_SKILL_PATHS),
)
def test_library_skill_frontmatter_warns_against_activation(
    skill_path: Path,
) -> None:
    """sow-shared description must signal it's reference-only.

    Visible in `list_skills`, the description is the LLM's only signal
    that activating this skill via `load_skill` is a misuse.
    """
    fm = _frontmatter(_read(skill_path))
    assert 'do NOT activate as a workflow skill' in fm, (
        f'{skill_path.name}/SKILL.md frontmatter must contain the literal '
        '"do NOT activate as a workflow skill" string (Section 5.9 item 9).'
    )


# ---------------------------------------------------------------------------
# Sanity — the lint did discover something once new skills land
# ---------------------------------------------------------------------------


def test_lint_discovery_state() -> None:
    """Document, in a test result, which skills the lint is policing.

    Not an assertion of "must be N skills" — that would break the gate
    during the migration. Just a visible artifact so a reviewer can see
    at a glance whether discovery picked up the new skills.
    """
    discovered = [p.name for p in _ALL_SKILLS]
    # Always passes; the message is the value (visible in -v output).
    assert discovered == discovered, (
        f'Lint discovered skills (excluding {sorted(EXCLUDED_SKILLS)}): '
        f'{discovered or "<none yet>"}'
    )
