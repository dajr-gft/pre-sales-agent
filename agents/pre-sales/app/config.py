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
        alias='GOOGLE_CLOUD_PROJECT'
    )
    LOCATION: str = Field(
        default='global',
        description='GCP location for Vertex AI services',
        alias='GOOGLE_CLOUD_LOCATION'
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
        default=1024,
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

    def resolve_project_id(self) -> str:
        """Return project_id, falling back to ADC if not explicitly set."""
        if self.PROJECT_ID:
            return self.PROJECT_ID
        _, project = google.auth.default()
        return project or ''


config = AgentConfig()
