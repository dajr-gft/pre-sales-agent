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
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # GCP -- auto-detected from ADC if not set
    project_id: str = Field(
        default="",
        description="GCP project ID (auto-detected from ADC if empty)",
    )
    location: str = Field(
        default="global",
        description="GCP location for Vertex AI services",
    )

    # Models
    gemini_model: str = Field(
        default="gemini-3.1-pro-preview",
        description="Primary Gemini model for the root agent",
    )

    # Agent identity
    company_name: str = Field(
        default="GFT Technologies",
        description="Partner company name injected into prompts",
    )

    # Generation
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="LLM temperature for content generation",
    )
    thinking_budget: int = Field(
        default=1024,
        ge=0,
        le=24576,
        description="Token budget for Gemini thinking mode",
    )

    # Resilience
    max_retries: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Max retry attempts for Gemini HTTP calls",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    log_json: bool = Field(
        default=True,
        description="JSON output for Cloud Logging (False for dev console)",
    )

    def resolve_project_id(self) -> str:
        """Return project_id, falling back to ADC if not explicitly set."""
        if self.project_id:
            return self.project_id
        _, project = google.auth.default()
        return project or ""


config = AgentConfig()
