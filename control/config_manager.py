import os
import sys
import yaml
import subprocess
from typing import Dict, Any, List, Tuple

class ConfigManager:
    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self.projects_path = os.path.join(config_dir, "projects.yaml")
        self.supabase_path = os.path.join(config_dir, "supabase.yaml")
        self.feature_flags_path = os.path.join(config_dir, "feature_flags.yaml")
        
        self.projects_config = {}
        self.supabase_config = {}
        self.feature_flags_config = {}
        self.load_all_configs()

    def load_all_configs(self):
        """Load YAML configuration files from config_dir."""
        if os.path.exists(self.projects_path):
            with open(self.projects_path, "r", encoding="utf-8") as f:
                self.projects_config = yaml.safe_load(f) or {}
                
        if os.path.exists(self.supabase_path):
            with open(self.supabase_path, "r", encoding="utf-8") as f:
                self.supabase_config = yaml.safe_load(f) or {}
                
        if os.path.exists(self.feature_flags_path):
            with open(self.feature_flags_path, "r", encoding="utf-8") as f:
                self.feature_flags_config = yaml.safe_load(f) or {}

    def get_version(self) -> str:
        """Return the configuration version."""
        return self.feature_flags_config.get("config_version", "unknown")

    def get_feature_flag(self, flag_name: str, default: bool = False) -> bool:
        """Fetch individual feature flags."""
        flags = self.feature_flags_config.get("feature_flags", {})
        return flags.get(flag_name, default)

    def validate_startup(self) -> Tuple[bool, List[str]]:
        """
        Validate all configurations, secrets, git environment, and workspaces.
        Returns (is_valid, error_messages).
        """
        errors = []

        # 1. Python Environment Check
        if sys.version_info < (3, 8):
            errors.append("CONFIG_ERR_001: Python version must be >= 3.8")

        # 2. Git Check
        try:
            subprocess.run(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except Exception:
            errors.append("CONFIG_ERR_002: Git executable is not installed or not in PATH")

        # 3. Projects Configuration Check
        if not os.path.exists(self.projects_path):
            errors.append("CONFIG_ERR_003: config/projects.yaml file is missing")
        elif not isinstance(self.projects_config, dict) or "projects" not in self.projects_config:
            errors.append("CONFIG_ERR_004: config/projects.yaml has invalid schema (missing 'projects' root)")

        # 4. Supabase Configuration Check
        if not os.path.exists(self.supabase_path):
            errors.append("CONFIG_ERR_005: config/supabase.yaml file is missing")
        else:
            if self.supabase_config.get("enabled", False):
                url = self.supabase_config.get("supabase_url") or os.environ.get("SUPABASE_URL")
                key = self.supabase_config.get("supabase_key") or os.environ.get("SUPABASE_SERVICE_KEY")
                if not url:
                    errors.append("CONFIG_ERR_006: Supabase enabled but URL is missing from config/env")
                if not key:
                    errors.append("CONFIG_ERR_007: Supabase enabled but service role key is missing from config/env")

        # 5. Secrets/Bridge Check
        bridge_token = os.environ.get("BRIDGE_TOKEN")
        if not bridge_token:
            errors.append("CONFIG_ERR_008: BRIDGE_TOKEN environment variable is not defined")

        # 6. Workspace Folders Check
        workspaces_dir = os.path.join(os.getcwd(), "workspaces")
        if not os.path.exists(workspaces_dir):
            try:
                os.makedirs(workspaces_dir, exist_ok=True)
            except Exception as e:
                errors.append(f"CONFIG_ERR_009: Workspaces folder could not be created: {str(e)}")

        return len(errors) == 0, errors
