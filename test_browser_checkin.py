"""Offline browser-flow regressions; no real account or network is used."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from browser_checkin import checkin_in_browser


class BrowserTimeout(Exception):
    pass


class Response:
    def __init__(self, path, payload, method="GET", origin="https://glados.cloud", status=200):
        self.url = origin + path
        self.request = SimpleNamespace(method=method)
        self.status = status
        self.ok = 200 <= status < 300
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class ResponseWaiter:
    def __init__(self, page, predicate):
        self.page, self.predicate, self.value = page, predicate, None

    def __enter__(self):
        self.page.waiter = self
        return self

    def __exit__(self, kind, value, traceback):
        self.page.waiter = None
        if kind is None and self.value is None:
            raise BrowserTimeout("private diagnostic must not be returned")


class Page:
    def __init__(self, payload=None, login=None, redirect=False, no_response=False):
        self.payload = payload if payload is not None else {"code": 0, "points": 2}
        self.login = login if login is not None else {"code": 0, "data": {"email": "fixture@example.test"}}
        self.redirect, self.no_response = redirect, no_response
        self.url, self.waiter, self.clicked = "", None, 0
        self.first = self

    def expect_response(self, predicate, **kwargs):
        return ResponseWaiter(self, predicate)

    def emit(self, response):
        if self.waiter and self.waiter.predicate(response):
            self.waiter.value = response

    def goto(self, url, **kwargs):
        self.url = "https://glados.cloud/login" if self.redirect else url
        if not self.redirect:
            self.emit(Response("/api/user/status", self.login))

    def get_by_role(self, role, name):
        if role != "button" or not name.search("Checkin"):
            raise AssertionError("Fixture check-in button was not selected")
        return self

    def wait_for(self, **kwargs):
        pass

    def click(self, **kwargs):
        if self.login.get("code") != 0 or self.redirect:
            raise AssertionError("Cannot check in before authentication")
        self.clicked += 1
        self.emit(Response("/api/user/checkin", {"code": 0, "points": 999}))
        self.emit(Response("/api/user/checkin", {"code": 0, "points": 999}, "POST",
                           "https://untrusted.example"))
        self.emit(Response("/api/user/checkin/other", {"code": 0, "points": 999}, "POST"))
        if not self.no_response:
            self.emit(Response("/api/user/checkin", self.payload, "POST"))


class BrowserFixture:
    def __init__(self, page):
        self.page = page
        self.context = SimpleNamespace(add_cookies=self.add_cookies, new_page=lambda: page,
                                       close=self.close_context)
        self.browser = SimpleNamespace(new_context=lambda **kwargs: self.context, close=self.close_browser)
        self.driver = SimpleNamespace(chromium=SimpleNamespace(launch=self.launch))
        self.cookies, self.launch_options = None, None
        self.context_closed = self.browser_closed = False
        self.module = SimpleNamespace(sync_playwright=lambda: self, TimeoutError=BrowserTimeout,
                                      Error=Exception)

    def __enter__(self):
        return self.driver

    def __exit__(self, *args):
        return False

    def launch(self, **kwargs):
        self.launch_options = kwargs
        return self.browser

    def add_cookies(self, cookies):
        self.cookies = cookies

    def close_context(self):
        self.context_closed = True

    def close_browser(self):
        self.browser_closed = True

    def run(self, **kwargs):
        with patch.dict(sys.modules, {"playwright": SimpleNamespace(), "playwright.sync_api": self.module}):
            return checkin_in_browser("glados.cloud", "koa:sess=fixture; koa:sess.sig=fixture-signature", **kwargs)


class BrowserCheckinTests(unittest.TestCase):
    def test_official_button_returns_only_matching_server_response(self):
        fixture = BrowserFixture(Page())
        result = fixture.run()
        self.assertEqual(result, {"code": 0, "points": 2})
        self.assertEqual(fixture.page.clicked, 1)
        self.assertTrue(fixture.context_closed and fixture.browser_closed)

    def test_cookies_are_host_scoped_in_an_isolated_context(self):
        fixture = BrowserFixture(Page())
        fixture.run(headless=False, channel="chrome")
        self.assertEqual([c["name"] for c in fixture.cookies], ["koa:sess", "koa:sess.sig"])
        self.assertTrue(all(c.get("url") == "https://glados.cloud/" for c in fixture.cookies))
        self.assertTrue(all("domain" not in c for c in fixture.cookies))
        self.assertFalse(fixture.launch_options["headless"])
        self.assertEqual(fixture.launch_options["channel"], "chrome")

    def test_duplicate_and_verification_failure_remain_server_results(self):
        for payload in ({"code": 1, "repeated": True, "points": 0},
                        {"code": 5, "reason": "turnstile", "points": 0}):
            with self.subTest(payload=payload):
                fixture = BrowserFixture(Page(payload=payload))
                self.assertEqual(fixture.run(), payload)
                self.assertEqual(fixture.page.clicked, 1)

    def test_invalid_login_never_clicks_checkin(self):
        for page in (Page(login={"code": 1}), Page(redirect=True)):
            with self.subTest(redirect=page.redirect):
                fixture = BrowserFixture(page)
                self.assertEqual(fixture.run()["code"], -2)
                self.assertEqual(page.clicked, 0)
                self.assertTrue(fixture.context_closed and fixture.browser_closed)

    def test_login_requires_a_profile_with_nonempty_email(self):
        for login in ({"code": 0}, {"code": 0, "data": []},
                      {"code": 0, "data": {}}, {"code": 0, "data": {"email": " "}},
                      {"code": 0, "data": {"email": 123}}):
            with self.subTest(login=login):
                fixture = BrowserFixture(Page(login=login))
                result = fixture.run()
                self.assertEqual(result["code"], -2)
                self.assertEqual(result["reason"], "login-required")
                self.assertEqual(fixture.page.clicked, 0)

    def test_http_status_is_classified_without_returning_response_body(self):
        for status, reason in ((401, "login-required"), (403, "http-error"),
                               (429, "rate-limited"), (500, "server-error"),
                               (503, "server-error")):
            for phase in ("status", "checkin"):
                with self.subTest(status=status, phase=phase):
                    class HttpFailurePage(Page):
                        def emit(self, response):
                            if response.url.endswith("/api/user/" + phase):
                                response.status, response.ok = status, False
                                response.payload = {"message": "private-response-body"}
                            super().emit(response)
                    fixture = BrowserFixture(HttpFailurePage())
                    result = fixture.run()
                    self.assertEqual(result["code"], -2)
                    self.assertEqual(result["reason"], reason)
                    self.assertIn(str(status), result["message"])
                    self.assertNotIn("private", result["message"])
                    self.assertTrue(fixture.context_closed and fixture.browser_closed)
                    if phase == "status":
                        self.assertEqual(fixture.page.clicked, 0)
                    if status == 403:
                        self.assertNotIn("更新登录 Cookie", result["message"])

    def test_malformed_json_and_schema_never_count_as_success(self):
        for payload in (ValueError("private body"), [], {"points": 2}, {"code": True}):
            with self.subTest(payload=payload):
                fixture = BrowserFixture(Page(payload=payload))
                result = fixture.run()
                self.assertEqual(result["code"], -2)
                self.assertNotIn("private", result["message"])
                self.assertTrue(fixture.context_closed and fixture.browser_closed)

    def test_missing_response_requires_manual_verification_without_retry(self):
        fixture = BrowserFixture(Page(no_response=True))
        result = fixture.run()
        self.assertEqual(result["code"], -2)
        self.assertIn("人工验证", result["message"])
        self.assertNotIn("private", result["message"])
        self.assertEqual(fixture.page.clicked, 1)
        self.assertTrue(fixture.context_closed and fixture.browser_closed)

    def test_browser_setup_failure_is_sanitized_and_owned_resources_are_closed(self):
        fixture = BrowserFixture(Page())
        def fail_to_add_cookies(cookies):
            raise RuntimeError("private cookie details")
        fixture.context.add_cookies = fail_to_add_cookies
        result = fixture.run()
        self.assertEqual(result["code"], -2)
        self.assertNotIn("private", result["message"])
        self.assertTrue(fixture.context_closed and fixture.browser_closed)

    def test_missing_playwright_returns_installation_hint(self):
        with patch.dict(sys.modules, {"playwright.sync_api": None}):
            result = checkin_in_browser("glados.cloud", "session=fixture")
        self.assertEqual(result["code"], -2)
        self.assertIn("Playwright", result["message"])

    def test_invalid_domain_and_empty_cookies_fail_before_browser_launch(self):
        for domain, cookies in (("glados.cloud.evil/path", "session=fixture"),
                                ("glados.cloud", ""), ("glados.cloud", "broken-cookie"),
                                ("glados.cloud", "bad@name=fixture")):
            with self.subTest(domain=domain, cookies=cookies):
                result = checkin_in_browser(domain, cookies)
                self.assertEqual(result["code"], -2)


if __name__ == "__main__":
    unittest.main()
