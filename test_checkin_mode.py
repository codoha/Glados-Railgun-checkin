"""Exercise browser selection through the existing multi-account runner."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from checkin import API, Checker, CheckinStatus, Config


class BrowserModeTests(unittest.TestCase):
    def config(self, **env):
        with patch.dict("os.environ", env, clear=True):
            return Config()

    def test_cli_defaults_to_official_browser(self):
        config = self.config()
        self.assertEqual(config.checkin_mode, "browser")
        self.assertTrue(config.browser_headless)

    def test_unknown_mode_fails_before_any_account_request(self):
        with self.assertRaises(ValueError):
            self.config(GLADOS_CHECKIN_MODE="browesr")

    def test_explicit_api_mode_and_headed_chrome(self):
        config = self.config(GLADOS_CHECKIN_MODE="api", GLADOS_BROWSER_HEADLESS="false",
                             GLADOS_BROWSER_CHANNEL="chrome")
        self.assertEqual(config.checkin_mode, "api")
        self.assertFalse(config.browser_headless)
        self.assertEqual(config.browser_channel, "chrome")

    def test_browser_response_drives_summary_without_legacy_submission(self):
        config = self.config(GLADOS_BROWSER_HEADLESS="false", GLADOS_BROWSER_CHANNEL="chrome")
        browser = Mock(return_value={"code": 0, "points": 2, "message": "Checkin! Got 2 Points"})
        with patch.dict("sys.modules", {"browser_checkin": SimpleNamespace(checkin_in_browser=browser)}), \
                patch.object(API, "get_status", return_value=("0 天", 0)), \
                patch.object(API, "get_points", side_effect=[("82 积分", 82), ("84 积分", 84)]), \
                patch.object(API, "_make_request", side_effect=AssertionError("legacy API was called")):
            result = Checker(config)._checkin_on_domain("test-cookie", 1, "glados.cloud")
        self.assertEqual(result.code, CheckinStatus.SUCCESS)
        self.assertEqual(result.points_delta, 2)
        browser.assert_called_once_with("glados.cloud", "test-cookie", headless=False, channel="chrome")

    def test_browser_rejection_is_not_retried_as_unverified_api(self):
        config = self.config()
        browser = Mock(return_value={"code": 5, "reason": "turnstile"})
        with patch.dict("sys.modules", {"browser_checkin": SimpleNamespace(checkin_in_browser=browser)}), \
                patch.object(API, "get_status", return_value=("0 天", 0)), \
                patch.object(API, "get_points", return_value=("82 积分", 82)), \
                patch.object(API, "_make_request", side_effect=AssertionError("legacy API was called")):
            result = Checker(config)._checkin_on_domain("test-cookie", 1, "glados.cloud")
        self.assertEqual(result.code, CheckinStatus.FAILURE)
        self.assertIn("人机验证", result.message)
        browser.assert_called_once()

    def test_browser_code_one_without_repeat_flag_is_a_failure(self):
        with API("glados.cloud", checkin_mode="browser") as api:
            result = api._parse_checkin({"code": 1, "message": "Please login"})
        self.assertEqual(result["code"], CheckinStatus.FAILURE)

    def test_browser_explicit_repeat_has_no_new_reward(self):
        with API("glados.cloud", checkin_mode="browser") as api:
            result = api._parse_checkin({"code": 0, "repeated": True, "points": 2})
        self.assertEqual(result["code"], CheckinStatus.REPEAT)
        self.assertEqual(result["points"], "0")


if __name__ == "__main__":
    unittest.main()
