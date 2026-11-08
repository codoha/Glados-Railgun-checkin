"""Regression tests for the public check-in challenge protocol (2026-11-08)."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from checkin import API, Checker, CheckinStatus, PLATFORM_UA


class CheckinServer:
    """A one-use challenge server; legacy requests still earn just one point."""

    proofs = {
        "challenge-1": "4cfadf169405fcace998cf8d9ff1a31a5b43e35e7b37204afd5972f3accc325c",
        "challenge-2": "59691b9c851b09f7d25e0c1223d10bef42314655bb5178da1ad36e156edd6735",
    }

    def __init__(self, mismatch=False, challenge_code=0, ttl=300000):
        self.now = 0.0
        self.challenges = {}
        self.submissions = []
        self.mismatch = mismatch
        self.challenge_code = challenge_code
        self.ttl = ttl
        self.clock = SimpleNamespace(monotonic=lambda: self.now, sleep=self.sleep)

    def sleep(self, seconds):
        self.now += seconds

    def request(self, url, method, data=None, cookies="", user_agent=None):
        if method == "GET" and url.endswith("/checkin/challenge"):
            nonce = "challenge-" + str(len(self.challenges) + 1)
            self.challenges[nonce] = (self.now, user_agent)
            payload = {"code": self.challenge_code, "nonce": nonce, "ttl": self.ttl}
        elif method == "POST" and url.endswith("/checkin"):
            self.submissions.append(dict(data))
            nonce = data.get("nonce")
            issued_at, issued_ua = self.challenges.get(nonce, (self.now, None))
            valid = (nonce in self.proofs and data.get("proof") == self.proofs[nonce]
                     and self.now - issued_at >= 2.5 and issued_ua == user_agent)
            if self.mismatch and user_agent != PLATFORM_UA["macOS"]:
                payload = {"code": 4, "reason": "device-mismatch", "loginDevice": "macOS"}
            else:
                payload = {"code": 0, "points": 11 if valid else 1, "message": "ok"}
        else:
            raise AssertionError("Unexpected request: " + method + " " + url)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode()
        return response


class ChallengeTests(unittest.TestCase):
    def run_checkin(self, server):
        with API("glados.cloud") as api:
            with patch.object(api, "_make_request", side_effect=server.request), \
                    patch("checkin.time", server.clock, create=True):
                return api.checkin("test-cookie")

    def test_current_protocol_earns_full_reward(self):
        result = self.run_checkin(CheckinServer())
        self.assertEqual(result["code"], CheckinStatus.SUCCESS)
        self.assertEqual(result["points"], "11")

    def test_device_retry_uses_fresh_challenge_with_matching_ua(self):
        server = CheckinServer(mismatch=True)
        result = self.run_checkin(server)
        self.assertEqual(result["points"], "11")
        self.assertEqual([p.get("nonce") for p in server.submissions],
                         ["challenge-1", "challenge-2"])

    def test_rejected_challenge_does_not_spend_daily_checkin(self):
        server = CheckinServer(challenge_code=1)
        result = self.run_checkin(server)
        self.assertNotEqual(result.get("code"), CheckinStatus.SUCCESS)
        self.assertEqual(server.submissions, [])

    def test_short_lived_challenge_does_not_submit_after_expiry(self):
        server = CheckinServer(ttl=1000)
        result = self.run_checkin(server)
        self.assertNotEqual(result.get("code"), CheckinStatus.SUCCESS)
        self.assertEqual(server.submissions, [])

    def test_transport_does_not_replay_one_use_post(self):
        with API("glados.cloud") as api:
            retry = api.session.get_adapter("https://").max_retries
            self.assertNotIn("POST", retry.allowed_methods)

    def test_checkin_uses_json_like_the_official_page(self):
        with API("glados.cloud") as api:
            with patch.object(api.session, "post") as post:
                post.return_value.ok = True
                api._make_request("https://glados.cloud/api/user/checkin", "POST",
                                  {"token": "glados.cloud", "nonce": "n", "proof": "p"})
                self.assertEqual(post.call_args.kwargs.get("json"),
                                 {"token": "glados.cloud", "nonce": "n", "proof": "p"})
                self.assertNotIn("data", post.call_args.kwargs)

    def test_repeat_flag_does_not_report_new_points(self):
        with API("glados.cloud") as api:
            result = api._parse_checkin({"code": 0, "repeated": True,
                                         "points": 11, "message": "already checked in"})
        self.assertEqual(result["code"], CheckinStatus.REPEAT)
        self.assertEqual(result["points"], "0")


class SummaryTests(unittest.TestCase):
    def test_default_log_and_push_show_actual_reward_and_balance_change(self):
        config = SimpleNamespace(exchange_plan="none", EXCHANGE_PLANS={}, verbose=False)
        checker = Checker(config)
        with patch.object(API, "get_status", return_value=("2 天", 0)), \
                patch.object(API, "get_points", side_effect=[("56 积分", 56), ("67 积分", 67)]), \
                patch.object(API, "checkin", return_value={"status": "签到成功",
                    "code": CheckinStatus.SUCCESS, "points": "11"}):
            result = checker._checkin_on_domain("test-cookie", 1, "glados.cloud")
        self.assertEqual(result.points, "11")
        checker.results = [result]
        _, push, log = checker.format_results()
        for text in (push, log):
            self.assertIn("本次:11 积分", text)
            self.assertIn("56 积分 → 67 积分", text)
            self.assertIn("变化:+11", text)

    def test_unavailable_before_balance_is_not_reported_as_zero(self):
        config = SimpleNamespace(exchange_plan="none", EXCHANGE_PLANS={}, verbose=False)
        checker = Checker(config)
        with patch.object(API, "get_status", return_value=("2 天", 0)), \
                patch.object(API, "get_points", side_effect=[("None 积分", 0), ("67 积分", 67)]), \
                patch.object(API, "checkin", return_value={"status": "签到成功",
                    "code": CheckinStatus.SUCCESS, "points": "11"}):
            checker.results = [checker._checkin_on_domain("test-cookie", 1, "glados.cloud")]
        _, _, log = checker.format_results()
        self.assertIn("变化:未知", log)


if __name__ == "__main__":
    unittest.main()
