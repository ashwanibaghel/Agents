import os
import yaml

def resolve_config() -> dict:
    """
    Resolves the configuration precedence order:
    1. APP_ENV (development / staging / production)
       - production / staging: defaults to Supabase enabled (True)
       - development: defaults to Supabase disabled (False)
    2. SUPABASE_ENABLED (optional environment override: true/1/false/0)
    3. config/supabase.yaml (only checked for 'enabled' in development mode)
    4. Default FALSE
    
    Returns a dict with:
      - app_env: str ("development", "staging", "production")
      - task_source: str ("supabase" or "local")
      - supabase_enabled: bool
      - configuration_source: str ("default", "environment", "yaml")
      - yaml_enabled: bool
      - env_override: bool
    """
    app_env = os.environ.get("APP_ENV", "development").lower()
    if app_env not in ("development", "staging", "production"):
        app_env = "development"

    # Default based on APP_ENV
    default_supabase = (app_env in ("production", "staging"))
    use_supabase = default_supabase
    config_source = "default"
    yaml_enabled = False
    env_override = False

    # Load YAML config to find yaml_enabled status (for diagnostics/reporting)
    supabase_cfg_path = "config/supabase.yaml"
    sb_yaml = {}
    if os.path.exists(supabase_cfg_path):
        try:
            with open(supabase_cfg_path, "r", encoding="utf-8") as f:
                sb_yaml = yaml.safe_load(f) or {}
            yaml_enabled = sb_yaml.get("enabled", False)
        except Exception:
            pass

    # Check precedence
    if os.environ.get("SUPABASE_ENABLED") is not None:
        env_val = os.environ.get("SUPABASE_ENABLED").lower()
        use_supabase = env_val in ("true", "1")
        config_source = "environment"
        env_override = True
    elif app_env == "development" and "enabled" in sb_yaml:
        use_supabase = yaml_enabled
        config_source = "yaml"
    else:
        use_supabase = default_supabase
        config_source = "default"

    return {
        "app_env": app_env,
        "task_source": "supabase" if use_supabase else "local",
        "supabase_enabled": use_supabase,
        "configuration_source": config_source,
        "yaml_enabled": yaml_enabled,
        "env_override": env_override
    }
