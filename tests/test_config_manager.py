import unittest
import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock
from control.config_manager import ConfigManager

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for config files
        self.test_dir = tempfile.mkdtemp()
        self.projects_path = os.path.join(self.test_dir, "projects.yaml")
        self.supabase_path = os.path.join(self.test_dir, "supabase.yaml")
        self.feature_flags_path = os.path.join(self.test_dir, "feature_flags.yaml")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def write_file(self, path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_load_all_configs(self):
        self.write_file(self.projects_path, "projects: {oi_labs: {active: true}}")
        self.write_file(self.supabase_path, "enabled: true\nsupabase_url: 'http://localhost'\nsupabase_key: 'abc'")
        self.write_file(self.feature_flags_path, "config_version: '3.2.0'\nfeature_flags: {metrics: true}")

        cm = ConfigManager(self.test_dir)
        self.assertEqual(cm.get_version(), "3.2.0")
        self.assertTrue(cm.get_feature_flag("metrics"))
        self.assertFalse(cm.get_feature_flag("persistent_sessions"))

    @patch("subprocess.run")
    @patch.dict(os.environ, {"BRIDGE_TOKEN": "secret_token"})
    def test_validate_startup_healthy(self, mock_sub_run):
        mock_sub_run.return_value = MagicMock(returncode=0)
        self.write_file(self.projects_path, "projects: {oi_labs: {active: true}}")
        self.write_file(self.supabase_path, "enabled: false")
        
        cm = ConfigManager(self.test_dir)
        is_valid, errors = cm.validate_startup()
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

    @patch("subprocess.run")
    @patch.dict(os.environ, {}, clear=True)
    def test_validate_startup_missing_token(self, mock_sub_run):
        mock_sub_run.return_value = MagicMock(returncode=0)
        self.write_file(self.projects_path, "projects: {oi_labs: {active: true}}")
        self.write_file(self.supabase_path, "enabled: false")
        
        cm = ConfigManager(self.test_dir)
        is_valid, errors = cm.validate_startup()
        self.assertFalse(is_valid)
        self.assertTrue(any("CONFIG_ERR_008" in err for err in errors))

    @patch("subprocess.run")
    @patch.dict(os.environ, {"BRIDGE_TOKEN": "secret_token", "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""})
    def test_validate_startup_missing_supabase(self, mock_sub_run):
        mock_sub_run.return_value = MagicMock(returncode=0)
        self.write_file(self.projects_path, "projects: {oi_labs: {active: true}}")
        self.write_file(self.supabase_path, "enabled: true")
        
        cm = ConfigManager(self.test_dir)
        is_valid, errors = cm.validate_startup()
        self.assertFalse(is_valid)
        self.assertTrue(any("CONFIG_ERR_006" in err for err in errors))

if __name__ == "__main__":
    unittest.main()
