"""Check the avatar account menu using synthetic HTTP responses, without a backend."""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def check(base_url: str, executable: str, screenshots: Path) -> None:
    screenshots.mkdir(parents=True, exist_ok=True)
    observations = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        for theme, width, language in [("dark", 1360, "zh"), ("light", 1360, "en"), ("dark", 390, "en"), ("light", 390, "zh")]:
            mobile, zh = width < 700, language == "zh"
            context = browser.new_context(viewport={"width": width, "height": 900}, has_touch=mobile)
            context.add_init_script("localStorage.setItem('research_access_token','browser-test-only');" + f"localStorage.setItem('research_language','{language}');localStorage.setItem('pskit-theme','{theme}');")
            page = context.new_page()
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            identity = {"id": "alice", "name": "Alice", "email": "alice@example.org", "is_anonymous": False, "avatar_revision": None}
            daily = [{"date": (date(2025, 10, 6) + timedelta(days=index)).isoformat(), "tokens": 0, "gpu_ms": 0} for index in range(365)]

            def api(route):
                request = route.request
                path = urlparse(request.url).path
                requests.append((request.method, path))
                if path.endswith("/auth/logout"):
                    route.fulfill(status=204)
                    return
                if path.endswith("/me"):
                    if request.method == "PATCH":
                        identity["name"] = request.post_data_json["name"]
                    data = identity
                elif path.endswith("/usage/activity"):
                    data = {"start_date": daily[0]["date"], "end_date": daily[-1]["date"], "timezone": "UTC", "days": daily}
                elif path.endswith("/usage"):
                    data = {metric: {"limit": 20000, "remaining": 20000, "used": 0, "reserved": 0, "resets_at": "2026-11-01T00:00:00Z"} for metric in ["tokens", "gpu"]}
                elif path.endswith("/g"):
                    data = [{"id": "project-alice", "name": "Personal", "description": ""}]
                else:
                    data = []
                route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

            def sidebar():
                if mobile:
                    page.get_by_role("button", name="打开侧栏" if zh else "Open sidebar", exact=True).click()
                    return page.locator(".mono-mobile-overlay")
                return page.locator(".mono-desktop-sidebar")

            def avatar(container, name="Alice"):
                return container.get_by_role("button", name=f"账号菜单：{name}" if zh else f"Account menu: {name}", exact=True)

            page.route("**/api/v1/**", api)
            page.goto(base_url)
            composer = page.get_by_role("textbox", name="消息内容" if zh else "Message", exact=True)
            expect(composer).to_be_visible()
            composer.fill("Unsent draft")
            expect(page.locator(".mono-topbar").get_by_role("link", name="设置" if zh else "Settings", exact=True)).to_have_count(0)
            container = sidebar()
            trigger = avatar(container)
            anchor = trigger.bounding_box()
            trigger.tap() if mobile else trigger.click()
            menu = page.get_by_role("menu", name="账号菜单" if zh else "Account menu", exact=True)
            expect(menu).to_be_visible()
            expect(menu.get_by_text("alice@example.org", exact=True)).to_be_visible()
            bounds = menu.bounding_box()
            assert bounds and anchor and bounds["width"] == 244, bounds
            assert bounds["y"] + bounds["height"] < anchor["y"], (bounds, anchor)
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width, bounds
            colors = menu.evaluate("e => ({ background: getComputedStyle(e).backgroundColor, color: getComputedStyle(e).color })")
            assert colors["background"] == ("rgb(38, 38, 38)" if theme == "dark" else "rgb(255, 255, 255)"), colors
            page.screenshot(path=str(screenshots / f"account-{theme}-{width}.png"))
            page.keyboard.press("Escape")
            expect(menu).to_have_count(0)
            expect(trigger).to_be_focused()
            trigger.press("Enter")
            profile = menu.get_by_role("menuitem", name="个人资料" if zh else "Profile", exact=True)
            expect(profile).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(menu.get_by_role("menuitem", name="设置" if zh else "Settings", exact=True)).to_be_focused()
            page.keyboard.press("Escape")
            trigger.click()
            page.mouse.click(width - 20, 100)
            expect(menu).to_have_count(0)
            expect(trigger).to_be_focused()
            expect(composer).to_have_value("Unsent draft")
            trigger.click()
            menu.get_by_role("menuitem", name="个人资料" if zh else "Profile", exact=True).click()
            nickname = page.get_by_role("textbox", name="昵称" if zh else "Nickname", exact=True)
            expect(nickname).to_be_focused()
            expect(page).to_have_url(f"{base_url}/settings#profile")
            expect(menu).to_have_count(0)
            expect(page.locator(".mono-mobile-overlay")).to_have_count(0)
            nickname.fill("Unchanged draft")
            page.locator(".settings-account-email").scroll_into_view_if_needed()
            assert page.locator(".mono-page-scroll").evaluate("e => e.scrollTop > 0")
            avatar(sidebar()).click()
            menu.get_by_role("menuitem", name="个人资料" if zh else "Profile", exact=True).click()
            expect(nickname).to_be_focused()
            expect(nickname).to_have_value("Unchanged draft")
            assert nickname.bounding_box()["y"] < 300
            page.get_by_role("button", name="保存昵称" if zh else "Save nickname", exact=True).click()
            container = sidebar()
            trigger = avatar(container, "Unchanged draft")
            expect(trigger).to_be_visible()
            if not mobile:
                container.get_by_role("button", name="收起侧栏" if zh else "Collapse sidebar", exact=True).click()
                expect(trigger).to_have_class("account-menu-trigger compact")
                button_bounds = trigger.bounding_box()
                assert button_bounds["width"] == 36, button_bounds
                assert trigger.locator(".user-avatar").bounding_box()["width"] == 30
            trigger.click()
            expect(menu.get_by_text("Unchanged draft", exact=True)).to_be_visible()
            menu.get_by_role("menuitem", name="设置" if zh else "Settings", exact=True).click()
            expect(page).to_have_url(f"{base_url}/settings")
            expect(page.locator(".mono-mobile-overlay")).to_have_count(0)
            avatar(sidebar(), "Unchanged draft").click()
            menu.get_by_role("menuitem", name="退出登录" if zh else "Sign out", exact=True).click()
            expect(page).to_have_url(f"{base_url}/login")
            assert ("POST", "/api/v1/auth/logout") in requests, requests
            assert page.evaluate("localStorage.getItem('research_access_token')") is None
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            observations.append({"theme": theme, "width": width, "language": language, "menu_bounds": bounds, "colors": colors, "page_errors": errors})
            context.close()
        browser.close()
    (screenshots / "inspection.json").write_text(json.dumps(observations, indent=2) + "\n")
    print(json.dumps(observations, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5197")
    parser.add_argument("--executable", required=True)
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-account-menu"))
    args = parser.parse_args()
    check(args.base_url.rstrip("/"), args.executable, args.screenshots)
