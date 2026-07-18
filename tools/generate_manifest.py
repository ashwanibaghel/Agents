#!/usr/bin/env python3
"""
tools/generate_manifest.py

Dynamically generates release_manifest.json containing exact git build info,
python environment information, database versions, and active configurations.
"""
import os
import sys
import json
import yaml
import platform
import subprocess
import datetime


def run_cmd(cmd: list) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)

    # 1. Git details
    git_commit = run_cmd(["git", "rev-parse", "HEAD"])
    git_branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    
    # Get current tag if on a tag, otherwise get the latest tag or default
    git_tag = run_cmd(["git", "describe", "--tags", "--exact-match"])
    if git_tag == "unknown":
        git_tag = run_cmd(["git", "describe", "--tags", "--abbrev=0"])
    if git_tag == "unknown":
        git_tag = "v3.2.0"  # Target tag for this release

    # 2. Config & Feature Flags
    feature_flags = {}
    ff_path = os.path.join(project_root, "config", "feature_flags.yaml")
    if os.path.exists(ff_path):
        try:
            with open(ff_path, "r", encoding="utf-8") as f:
                feature_flags = yaml.safe_load(f) or {}
        except Exception:
            pass

    # 3. Environment requirements (.env.example keys)
    required_env = []
    env_ex_path = os.path.join(project_root, ".env.example")
    if os.path.exists(env_ex_path):
        try:
            with open(env_ex_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key = line.split("=")[0].strip()
                        required_env.append(key)
        except Exception:
            pass

    # 4. Generate manifest structure
    manifest = {
        "version": "3.2.0",
        "release_date": datetime.datetime.utcnow().isoformat() + "Z",
        "git_commit": git_commit,
        "git_tag": git_tag,
        "branch": git_branch,
        "python_version": platform.python_version(),
        "database_version": "1.0.0",
        "config_version": "3.2",
        "supported_platforms": ["Linux", "Windows", "macOS"],
        "feature_flags": feature_flags,
        "required_environment": required_env,
        "schema_versions": {
            "sqlite": "3.2.0",
            "supabase": "3.2.0"
        }
    }

    out_path = os.path.join(project_root, "release_manifest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[OK] Generated release_manifest.json at {out_path}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
