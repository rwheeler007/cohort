"""
BOSS Communications Service - Project Settings Manager.

Manages per-project configurations for calendar and social media integrations.
Allows multiple companies (ChillGuard, PartSpec, etc.) to have separate OAuth tokens,
calendars, and social media accounts.

IMPORTANT: No Unicode emojis - Windows cp1252 encoding only.
Use [OK], [!], [X], [*], [>>] for status indicators.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProjectCalendarConfig(BaseModel):
    """Calendar configuration for a single project."""
    project_id: str
    project_name: str
    google_credentials_file: str  # Path relative to config directory
    google_tokens_file: str  # Path relative to config directory
    calendar_id: Optional[str] = None  # Default calendar or specific calendar ID
    enabled: bool = True


class ProjectSocialConfig(BaseModel):
    """Social media configuration for a single project."""
    project_id: str
    project_name: str
    platforms: Dict[str, Dict] = Field(default_factory=dict)  # platform -> token data
    enabled: bool = True


class ProjectConfig(BaseModel):
    """Complete configuration for a single project."""
    project_id: str
    project_name: str
    display_name: str
    color: str = "#667eea"  # UI color for project tags
    calendar: Optional[ProjectCalendarConfig] = None
    social: Optional[ProjectSocialConfig] = None
    metadata: Dict = Field(default_factory=dict)


class ProjectSettingsManager:
    """Manages project-specific settings for calendar and social media."""

    def __init__(self, base_path: Path):
        """Initialize the project settings manager.

        Args:
            base_path: Path to BOSS root directory
        """
        self.base_path = Path(base_path)
        self.config_dir = self.base_path / "data" / "comms_service" / "config"
        self.projects_config_file = self.config_dir / "projects.json"

        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Load or initialize projects configuration
        self.projects = self._load_projects()

        logger.info("[OK] ProjectSettingsManager initialized")

    def _load_projects(self) -> Dict[str, ProjectConfig]:
        """Load projects configuration from disk.

        Returns:
            Dict mapping project_id to ProjectConfig
        """
        if not self.projects_config_file.exists():
            # Create default configuration with example projects
            default_projects = {
                "chillguard": ProjectConfig(
                    project_id="chillguard",
                    project_name="chillguard",
                    display_name="ChillGuard",
                    color="#3b82f6",
                    calendar=ProjectCalendarConfig(
                        project_id="chillguard",
                        project_name="ChillGuard",
                        google_credentials_file="google_credentials_chillguard.json",
                        google_tokens_file="google_tokens_chillguard.json",
                        enabled=False
                    ),
                    social=ProjectSocialConfig(
                        project_id="chillguard",
                        project_name="ChillGuard",
                        platforms={},
                        enabled=False
                    )
                ),
                "partspec": ProjectConfig(
                    project_id="partspec",
                    project_name="partspec",
                    display_name="PartSpec.ai",
                    color="#f59e0b",
                    calendar=ProjectCalendarConfig(
                        project_id="partspec",
                        project_name="PartSpec.ai",
                        google_credentials_file="google_credentials_partspec.json",
                        google_tokens_file="google_tokens_partspec.json",
                        enabled=False
                    ),
                    social=ProjectSocialConfig(
                        project_id="partspec",
                        project_name="PartSpec.ai",
                        platforms={},
                        enabled=False
                    )
                ),
                "patent": ProjectConfig(
                    project_id="patent",
                    project_name="patent",
                    display_name="Patent Intel",
                    color="#8b5cf6",
                    calendar=ProjectCalendarConfig(
                        project_id="patent",
                        project_name="Patent Intel",
                        google_credentials_file="google_credentials_patent.json",
                        google_tokens_file="google_tokens_patent.json",
                        enabled=False
                    ),
                    social=ProjectSocialConfig(
                        project_id="patent",
                        project_name="Patent Intel",
                        platforms={},
                        enabled=False
                    )
                )
            }
            self._save_projects(default_projects)
            return default_projects

        try:
            with open(self.projects_config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    project_id: ProjectConfig(**project_data)
                    for project_id, project_data in data.items()
                }
        except Exception as exc:
            logger.error("[X] Failed to load projects config: %s", exc)
            return {}

    def _save_projects(self, projects: Dict[str, ProjectConfig]) -> None:
        """Save projects configuration to disk.

        Args:
            projects: Dict mapping project_id to ProjectConfig
        """
        try:
            data = {
                project_id: project.model_dump()
                for project_id, project in projects.items()
            }
            with open(self.projects_config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info("[OK] Saved projects configuration")
        except Exception as exc:
            logger.error("[X] Failed to save projects config: %s", exc)

    def list_projects(self) -> List[ProjectConfig]:
        """Get list of all configured projects.

        Returns:
            List of ProjectConfig objects
        """
        return list(self.projects.values())

    def get_project(self, project_id: str) -> Optional[ProjectConfig]:
        """Get configuration for a specific project.

        Args:
            project_id: Project identifier (e.g., 'chillguard', 'partspec')

        Returns:
            ProjectConfig or None if not found
        """
        return self.projects.get(project_id)

    def add_project(self, config: ProjectConfig) -> bool:
        """Add or update a project configuration.

        Args:
            config: ProjectConfig to add/update

        Returns:
            True if successful, False otherwise
        """
        try:
            self.projects[config.project_id] = config
            self._save_projects(self.projects)
            logger.info("[OK] Added/updated project: %s", config.project_id)
            return True
        except Exception as exc:
            logger.error("[X] Failed to add project: %s", exc)
            return False

    def update_project(self, config: ProjectConfig) -> bool:
        """Update an existing project configuration.

        Args:
            config: ProjectConfig with updated values

        Returns:
            True if successful, False if project not found
        """
        if config.project_id not in self.projects:
            logger.error("[X] Project not found for update: %s", config.project_id)
            return False

        try:
            self.projects[config.project_id] = config
            self._save_projects(self.projects)
            logger.info("[OK] Updated project: %s", config.project_id)
            return True
        except Exception as exc:
            logger.error("[X] Failed to update project: %s", exc)
            return False

    def remove_project(self, project_id: str) -> bool:
        """Remove a project configuration.

        Args:
            project_id: Project identifier to remove

        Returns:
            True if removed, False if not found
        """
        if project_id not in self.projects:
            return False

        del self.projects[project_id]
        self._save_projects(self.projects)
        logger.info("[OK] Removed project: %s", project_id)
        return True

    def get_calendar_config(self, project_id: str) -> Optional[ProjectCalendarConfig]:
        """Get calendar configuration for a project.

        Args:
            project_id: Project identifier

        Returns:
            ProjectCalendarConfig or None if not found/disabled
        """
        project = self.get_project(project_id)
        if not project or not project.calendar:
            return None
        return project.calendar if project.calendar.enabled else None

    def get_social_config(self, project_id: str) -> Optional[ProjectSocialConfig]:
        """Get social media configuration for a project.

        Args:
            project_id: Project identifier

        Returns:
            ProjectSocialConfig or None if not found/disabled
        """
        project = self.get_project(project_id)
        if not project or not project.social:
            return None
        return project.social if project.social.enabled else None

    def set_calendar_enabled(self, project_id: str, enabled: bool) -> bool:
        """Enable or disable calendar integration for a project.

        Args:
            project_id: Project identifier
            enabled: True to enable, False to disable

        Returns:
            True if successful, False if project not found
        """
        project = self.get_project(project_id)
        if not project or not project.calendar:
            return False

        project.calendar.enabled = enabled
        self.projects[project_id] = project
        self._save_projects(self.projects)
        logger.info("[OK] Set calendar enabled=%s for project: %s", enabled, project_id)
        return True

    def set_social_enabled(self, project_id: str, enabled: bool) -> bool:
        """Enable or disable social media integration for a project.

        Args:
            project_id: Project identifier
            enabled: True to enable, False to disable

        Returns:
            True if successful, False if project not found
        """
        project = self.get_project(project_id)
        if not project or not project.social:
            return False

        project.social.enabled = enabled
        self.projects[project_id] = project
        self._save_projects(self.projects)
        logger.info("[OK] Set social enabled=%s for project: %s", enabled, project_id)
        return True

    def update_social_platform_token(
        self,
        project_id: str,
        platform: str,
        token_data: Dict
    ) -> bool:
        """Update social media platform token for a project.

        Args:
            project_id: Project identifier
            platform: Platform name (e.g., 'twitter', 'linkedin')
            token_data: OAuth token data

        Returns:
            True if successful, False if project not found
        """
        project = self.get_project(project_id)
        if not project or not project.social:
            return False

        project.social.platforms[platform] = token_data
        self.projects[project_id] = project
        self._save_projects(self.projects)
        logger.info("[OK] Updated %s token for project: %s", platform, project_id)
        return True

    def get_social_platform_token(
        self,
        project_id: str,
        platform: str
    ) -> Optional[Dict]:
        """Get social media platform token for a project.

        Args:
            project_id: Project identifier
            platform: Platform name (e.g., 'twitter', 'linkedin')

        Returns:
            Token data dict or None if not found
        """
        social_config = self.get_social_config(project_id)
        if not social_config:
            return None
        return social_config.platforms.get(platform)

    def get_calendar_paths(self, project_id: str) -> Optional[Dict[str, Path]]:
        """Get calendar credential and token file paths for a project.

        Args:
            project_id: Project identifier

        Returns:
            Dict with 'credentials' and 'tokens' paths, or None if not configured
        """
        calendar_config = self.get_calendar_config(project_id)
        if not calendar_config:
            return None

        return {
            "credentials": self.config_dir / calendar_config.google_credentials_file,
            "tokens": self.config_dir / calendar_config.google_tokens_file,
        }

    def detect_project_from_metadata(self, metadata: Dict) -> str:
        """Detect project ID from metadata fields.

        Looks for project indicators in metadata.project, campaign_id, etc.

        Args:
            metadata: Metadata dictionary from draft/event/post

        Returns:
            Detected project_id or first available project as fallback
        """
        # Direct project field
        if "project" in metadata:
            project = metadata["project"].lower()
            if project in self.projects:
                return project

        # Check campaign_id
        if "campaign_id" in metadata:
            campaign_id = metadata["campaign_id"].lower()
            for project_id in self.projects.keys():
                if project_id in campaign_id:
                    return project_id

        # Fallback to first available project (or "chillguard" if available)
        if "chillguard" in self.projects:
            return "chillguard"
        elif self.projects:
            return list(self.projects.keys())[0]
        else:
            logger.warning("[!] No projects configured - using placeholder 'default'")
            return "default"
