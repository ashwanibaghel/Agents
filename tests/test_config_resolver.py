import unittest
import os
import tempfile
import shutil
import yaml
from unittest.mock import patch
from control.config_resolver import resolve_config

class TestConfigResolver(unittest.TestCase):
    def setUp(self):
        # Backup env
        self.old_env = dict(os.environ)
        # Create temp dir for yaml
        self.temp_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        os.chdir(self.temp_dir)
        os.makedirs("config", exist_ok=True)
        self.yaml_path = "config/supabase.yaml"

    def tearDown(self):
        # Restore env
        os.environ.clear()
        os.environ.update(self.old_env)
        # Restore CWD and delete temp dir
        os.chdir(self.old_cwd)
        shutil.rmtree(self.temp_dir)

    def write_yaml(self, enabled):
        with open(self.yaml_path, "w", encoding="utf-8") as f:
            yaml.dump({"enabled": enabled, "supabase_url": "http://mock", "supabase_key": "mock"}, f)

    def test_default_development_no_yaml(self):
        if "APP_ENV" in os.environ:
            del os.environ["APP_ENV"]
        if "SUPABASE_ENABLED" in os.environ:
            del os.environ["SUPABASE_ENABLED"]
        res = resolve_config()
        self.assertEqual(res["app_env"], "development")
        self.assertEqual(res["task_source"], "local")
        self.assertFalse(res["supabase_enabled"])
        self.assertEqual(res["configuration_source"], "default")

    def test_production_default(self):
        os.environ["APP_ENV"] = "production"
        if "SUPABASE_ENABLED" in os.environ:
            del os.environ["SUPABASE_ENABLED"]
        res = resolve_config()
        self.assertEqual(res["app_env"], "production")
        self.assertEqual(res["task_source"], "supabase")
        self.assertTrue(res["supabase_enabled"])
        self.assertEqual(res["configuration_source"], "default")

    def test_production_override_false(self):
        os.environ["APP_ENV"] = "production"
        os.environ["SUPABASE_ENABLED"] = "false"
        res = resolve_config()
        self.assertEqual(res["app_env"], "production")
        self.assertEqual(res["task_source"], "local")
        self.assertFalse(res["supabase_enabled"])
        self.assertEqual(res["configuration_source"], "environment")
        self.assertTrue(res["env_override"])

    def test_development_yaml_enabled(self):
        os.environ["APP_ENV"] = "development"
        if "SUPABASE_ENABLED" in os.environ:
            del os.environ["SUPABASE_ENABLED"]
        self.write_yaml(True)
        res = resolve_config()
        self.assertEqual(res["app_env"], "development")
        self.assertEqual(res["task_source"], "supabase")
        self.assertTrue(res["supabase_enabled"])
        self.assertEqual(res["configuration_source"], "yaml")
        self.assertTrue(res["yaml_enabled"])

    def test_production_yaml_ignored(self):
        os.environ["APP_ENV"] = "production"
        if "SUPABASE_ENABLED" in os.environ:
            del os.environ["SUPABASE_ENABLED"]
        self.write_yaml(False) # YAML says disabled, but in prod we default to true!
        res = resolve_config()
        self.assertEqual(res["app_env"], "production")
        self.assertEqual(res["task_source"], "supabase")
        self.assertTrue(res["supabase_enabled"])
        self.assertEqual(res["configuration_source"], "default")
        self.assertFalse(res["yaml_enabled"]) # YAML reads false, but decision is True

if __name__ == "__main__":
    unittest.main()
