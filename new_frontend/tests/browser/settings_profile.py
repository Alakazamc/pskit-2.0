"""Inspect profile and yearly usage UI in both themes using synthetic HTTP data."""

import argparse
import base64
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
            context = browser.new_context(viewport={"width": width, "height": 900})
            context.add_init_script("localStorage.setItem('research_access_token','browser-test-only');" + f"localStorage.setItem('research_language','{language}');localStorage.setItem('pskit-theme','{theme}');")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            identity = {"id": "alice", "name": "Alice", "email": "alice@example.org", "avatar_revision": None}
            image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
            start = date(2025, 10, 6)
            daily = [{"date": (start + timedelta(days=index)).isoformat(), "tokens": (index % 5 + 1) * 100 if index > 250 and index % 7 < 5 else 0, "gpu_ms": 120000 if index % 17 == 0 else 0} for index in range(365)]

            def api(route):
                request = route.request
                path = urlparse(request.url).path
                if path.endswith("/me/avatar"):
                    if request.method == "PUT":
                        identity["avatar_revision"] = "a" * 32
                        data = identity
                    elif request.method == "DELETE":
                        identity["avatar_revision"] = None
                        data = identity
                    else:
                        route.fulfill(status=200, content_type="image/png", body=image)
                        return
                elif path.endswith("/me"):
                    if request.method == "PATCH":
                        identity["name"] = request.post_data_json["name"]
                    data = identity
                elif path.endswith("/usage/activity"):
                    data = {"start_date": daily[0]["date"], "end_date": daily[-1]["date"], "timezone": "UTC", "days": daily}
                elif path.endswith("/usage"):
                    data = {"tokens": {"limit": 20000, "remaining": 16350, "used": 3650, "reserved": 0, "resets_at": "2026-11-01T00:00:00Z"}, "gpu": {"limit": 60, "remaining": 40, "used": 20, "reserved": 0, "resets_at": "2026-10-06T00:00:00Z"}}
                elif path.endswith("/g"):
                    data = [{"id": "project-alice", "name": "Personal", "description": ""}]
                else:
                    data = []
                route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

            page.route("**/api/v1/**", api)
            page.goto(f"{base_url}/settings")
            zh = language == "zh"
            nickname = page.get_by_role("textbox", name="昵称" if zh else "Nickname", exact=True)
            expect(nickname).to_have_value("Alice")
            nickname.fill("小林 Research")
            page.get_by_role("button", name="保存昵称" if zh else "Save nickname", exact=True).click()
            expect(page.locator(".mono-sidebar-footer b").first).to_have_text("小林 Research")
            page.reload()
            expect(nickname).to_have_value("小林 Research")
            page.get_by_label("上传头像" if zh else "Upload avatar", exact=True).set_input_files({"name": "avatar.png", "mimeType": "image/png", "buffer": image})
            expect(page.locator(".profile-avatar-row img")).to_be_visible()
            expect(page.get_by_role("button", name="移除头像" if zh else "Remove avatar", exact=True)).to_be_visible()
            page.screenshot(path=str(screenshots / f"profile-{theme}-{width}.png"))
            calendar = page.locator(".usage-activity")
            calendar.scroll_into_view_if_needed()
            expect(calendar.locator(".usage-activity-cell")).to_have_count(365)
            bounds = calendar.locator(".usage-activity-cell").last.bounding_box()
            assert bounds and bounds["width"] == bounds["height"] == 14, bounds
            expect(calendar.locator("[data-level='4']").first).to_be_visible()
            last = calendar.locator(".usage-activity-cell").last
            last.focus()
            page.keyboard.press("ArrowUp")
            expect(calendar.locator(".usage-activity-cell").nth(363)).to_be_focused()
            page.get_by_role("button", name="GPU 活动" if zh else "GPU activity", exact=True).click()
            expect(page.get_by_test_id("usage-activity-details")).to_contain_text("GPU 分钟" if zh else "GPU minutes")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator(".mono-page-scroll").evaluate("e => e.scrollHeight > e.clientHeight")
            expect(calendar.locator(".usage-activity-legend")).to_be_visible()
            page.screenshot(path=str(screenshots / f"activity-{theme}-{width}.png"))
            page.get_by_role("button", name="移除头像" if zh else "Remove avatar", exact=True).click()
            expect(page.locator(".profile-avatar-row img")).to_have_count(0)
            assert not errors, errors
            observations.append({"theme": theme, "width": width, "language": language, "cells": 365, "cell_size": bounds, "page_errors": errors})
            context.close()
        browser.close()
    (screenshots / "inspection.json").write_text(json.dumps(observations, indent=2) + "\n")
    print(json.dumps(observations, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5197")
    parser.add_argument("--executable", required=True)
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-settings-profile"))
    args = parser.parse_args()
    check(args.base_url.rstrip("/"), args.executable, args.screenshots)
