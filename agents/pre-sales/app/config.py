from __future__ import annotations

import google.auth
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    """Centralized, validated configuration.

    Reads from environment variables with AGENT_ prefix.
    Falls back to .env file. Validates on startup.
    """

    model_config = SettingsConfigDict(
        env_prefix='AGENT_',
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    # GCP
    PROJECT_ID: str = Field(
        default='',
        description='GCP project ID (auto-detected from ADC if empty)',
        alias='GOOGLE_CLOUD_PROJECT',
    )
    LOCATION: str = Field(
        default='global',
        description='GCP location for Vertex AI services',
        alias='GOOGLE_CLOUD_LOCATION',
    )
    GOOGLE_GENAI_USE_VERTEXAI: bool = Field(
        default=True,
        description='Whether to use Vertex AI for GenAI services',
    )
    LOGS_BUCKET_NAME: str = Field(
        default='',
        description='GCS Bucket name for logs storage',
    )

    # Models
    GEMINI_MODEL: str = Field(
        default='gemini-3.1-pro-preview',
        description='Primary Gemini model for the root agent',
    )
    VALIDATION_SKILL_MODEL: str = Field(
        default='gemini-flash-latest',
        description=(
            'Model used by each of the 5 semantic skills inside '
            'validation_critic. Default is Flash because skills are '
            'tightly scoped and benefit from low latency / low cost.'
        ),
    )
    VALIDATION_SKILL_THINKING_BUDGET: int = Field(
        default=1024,
        ge=0,
        le=24576,
        description=(
            'Thinking budget for the 5 semantic skills. Medium recovers '
            'recall lost by zero-thinking Flash on cross-section reasoning.'
        ),
    )
    VALIDATION_SUMMARY_MODEL: str = Field(
        default='gemini-flash-lite-latest',
        description=(
            'Model used by ValidationSummarySkill (text-only summary). '
            'Flash-Lite is enough because the skill only writes 2 fields.'
        ),
    )
    VALIDATION_SUMMARY_THINKING_BUDGET: int = Field(
        default=1024,
        ge=0,
        le=24576,
        description='Thinking budget for ValidationSummarySkill.',
    )

    # Agent identity
    COMPANY_NAME: str = Field(
        default='GFT Technologies',
        description='Partner company name injected into prompts',
    )

    # Generation
    TEMPERATURE: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description='LLM temperature for content generation',
    )
    THINKING_BUDGET: int = Field(
        default=2048,
        ge=0,
        le=24576,
        description='Token budget for Gemini thinking mode',
    )

    # Resilience
    MAX_RETRIES: int = Field(
        default=3,
        ge=1,
        le=20,
        description='Max retry attempts for Gemini HTTP calls',
    )

    # Logging
    LOG_LEVEL: str = Field(
        default='INFO',
        description='Logging level (DEBUG, INFO, WARNING, ERROR)',
    )
    LOG_JSON: bool = Field(
        default=True,
        description='JSON output for Cloud Logging (False for dev console)',
    )

    # Logo
    LOGO_DEV_PUBLISHABLE_KEY: str | None = Field(
        default=None,
        description="Publishable API key from logo.dev for fetching customer logos in SOW documents",
        alias='LOGO_DEV_PUBLISHABLE_KEY',
    )

    # Safety — content filters (Layer 2)
    SAFETY_HARM_BLOCK_THRESHOLD: str = Field(
        default='BLOCK_MEDIUM_AND_ABOVE',
        description=(
            'Gemini configurable harm-category block threshold. One of '
            'BLOCK_NONE, BLOCK_ONLY_HIGH, BLOCK_MEDIUM_AND_ABOVE, '
            'BLOCK_LOW_AND_ABOVE, OFF.'
        ),
    )

    # Safety — scope/injection guardrail (Layer 3)
    SAFETY_GUARDRAIL_ENABLED: bool = Field(
        default=True,
        description=(
            'Enable the LLM-as-a-judge scope/injection guardrail that runs as '
            'before_model_callback on the root agent.'
        ),
    )
    SAFETY_JUDGE_MODEL: str = Field(
        default='gemini-flash-lite-latest',
        description=(
            'Model used by the scope guardrail to classify user input as '
            'on-topic / off-topic / injection. Should be a fast, cheap model.'
        ),
    )
    SAFETY_JUDGE_TIMEOUT_S: float = Field(
        default=8.0,
        ge=1.0,
        le=30.0,
        description='Timeout (seconds) for the judge model call. Fails open on timeout.',
    )

    def resolve_project_id(self) -> str:
        """Return project_id, falling back to ADC if not explicitly set."""
        if self.PROJECT_ID:
            return self.PROJECT_ID
        _, project = google.auth.default()
        return project or ''


config = AgentConfig()
