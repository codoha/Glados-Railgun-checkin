"""Check in through the official page without manufacturing verification data."""
import re
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit


def _failure(message, reason="browser-error"):
    return {"code": -2, "points": 0, "reason": reason, "message": message}


def checkin_in_browser(domain, cookies, headless=True, channel=None, timeout_ms=120000):
    """Return the official POST response; headed mode permits manual verification.

    Each call owns an ephemeral browser and context. No trace, screenshot, cookie,
    request body, or browser exception text is logged or persisted.
    """
    if (not isinstance(domain, str) or not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain)):
        return _failure("签到域名无效", "invalid-config")
    if not isinstance(timeout_ms, (int, float)) or not 0 < timeout_ms <= 600000:
        return _failure("浏览器超时配置无效", "invalid-config")
    origin = "https://" + domain.lower()
    try:
        jar = SimpleCookie()
        if not isinstance(cookies, str):
            raise ValueError("invalid cookies")
        jar.load(cookies)
        if not jar:
            raise ValueError("empty cookies")
        browser_cookies = [{"name": item.key, "value": item.value,
                            "url": origin + "/", "secure": True}
                           for item in jar.values()]
    except (CookieError, ValueError, TypeError):
        return _failure("未配置有效的登录 Cookie", "invalid-config")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    except ImportError:
        return _failure("请安装 Playwright 及 Chromium 浏览器后重试", "browser-unavailable")

    deadline = time.monotonic() + timeout_ms / 1000
    browser = context = page = None

    def remaining():
        return max(1, int((deadline - time.monotonic()) * 1000))

    def matches(response, path, method):
        url = urlsplit(response.url)
        return (url.scheme == "https" and url.netloc == domain.lower()
                and url.path == path and response.request.method == method)

    def on_console():
        url = urlsplit(page.url)
        return (url.scheme == "https" and url.netloc == domain.lower()
                and (url.path == "/console" or url.path.startswith("/console/")))

    try:
        with sync_playwright() as playwright:
            try:
                launch = {"headless": headless, "timeout": remaining()}
                if channel:
                    launch["channel"] = channel
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context()
                context.add_cookies(browser_cookies)
                page = context.new_page()
                # Register before navigation so an early authentication response is retained.
                with page.expect_response(
                    lambda r: matches(r, "/api/user/status", "GET"), timeout=remaining()
                ) as login_response:
                    page.goto(origin + "/console/checkin", wait_until="domcontentloaded",
                              timeout=remaining())
                if not on_console():
                    return _failure("官网已跳转到登录页，请更新登录 Cookie", "login-required")
                try:
                    login = login_response.value.json()
                except Exception:
                    return _failure("官网登录状态返回非 JSON，请在官网检查登录或验证", "login-required")
                if (not login_response.value.ok or not isinstance(login, dict)
                        or type(login.get("code")) is not int or login["code"] != 0):
                    return _failure("官网登录状态无效，请更新登录 Cookie", "login-required")

                button = page.get_by_role(
                    "button", name=re.compile(r"^\s*(?:Check\s*in|签到)\s*$", re.I)
                ).first
                button.wait_for(state="visible", timeout=remaining())
                # The page obtains its own nonce, proof and Turnstile token.
                # Playwright only clicks the normal check-in button, never the CAPTCHA.
                with page.expect_response(
                    lambda r: matches(r, "/api/user/checkin", "POST"), timeout=remaining()
                ) as checkin_response:
                    button.click(timeout=remaining())
                if not on_console():
                    return _failure("签到时登录已失效，请更新登录 Cookie", "login-required")
                response = checkin_response.value
                if not response.ok:
                    return _failure("官网签到请求失败，请在官网检查登录或人工验证", "needs-verification")
                try:
                    result = response.json()
                except Exception:
                    return _failure("官网签到返回非 JSON，未确认签到成功", "invalid-response")
                if not isinstance(result, dict) or type(result.get("code")) is not int:
                    return _failure("官网签到响应格式异常，未确认签到成功", "invalid-response")
                return result
            finally:
                for resource in (context, browser):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception:
                            pass
    except BrowserTimeout:
        if page is not None:
            url = urlsplit(page.url)
            if url.path.startswith(("/login", "/signin")):
                return _failure("官网已跳转到登录页，请更新登录 Cookie", "login-required")
        return _failure(
            "等待官网签到超时，未确认签到成功；请打开官网完成人工验证后重试，或使用有界面模式",
            "needs-verification",
        )
    except Exception:
        return _failure("浏览器签到未能完成；请确认浏览器已安装，并在官网检查登录或人工验证")
