"""Verify local chat draft/preferences with synthetic HTTP responses, never live models."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def check(base_url: str, executable: str, screenshots: Path) -> None:
    screenshots.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        for theme, width in [("dark", 1360), ("light", 1360), ("dark", 390), ("light", 390)]:
            context = browser.new_context(viewport={"width": width, "height": 850})
            context.add_init_script(
                "localStorage.setItem('research_access_token', 'browser-test-only');"
                "localStorage.setItem('research_language', 'zh');"
                f"localStorage.setItem('pskit-theme', '{theme}');"
            )
            page = context.new_page()
            errors, writes = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            account = {"id": "alice"}

            def api(route):
                request = route.request
                path = urlparse(request.url).path
                if request.method != "GET":
                    writes.append({"method": request.method, "path": path})
                identity = account["id"]
                if path.endswith("/me"):
                    data = {"id": identity, "name": identity, "email": f"{identity}@example.org"}
                elif path.endswith("/g"):
                    data = [{"id": f"project-{identity}", "name": "Personal", "description": ""}]
                elif path.endswith("/c"):
                    data = [{"id": session, "project_id": f"project-{identity}", "title": f"Chat {session}", "status": "idle"} for session in ["a", "b"]]
                elif path.endswith("/models"):
                    data = [{"id": model, "supports_images": True, "reasoning_levels": ["medium", "high"]} for model in ["first-model", "second-model"]]
                else:
                    data = []
                route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

            page.route("**/api/v1/**", api)
            page.goto(f"{base_url}/session/a")
            textbox = page.get_by_label("消息内容")
            textbox.fill("A-local-draft")
            page.get_by_role("button", name=re.compile("选择模型与推理强度")).click()
            page.get_by_role("button", name=re.compile("切换模型")).click()
            page.get_by_role("button", name="second-model", exact=True).click()
            page.get_by_role("slider", name="推理强度").press("End")
            page.keyboard.press("Escape")
            expect(page.get_by_role("button", name=re.compile("选择模型与推理强度"))).to_contain_text("second-model高")

            def navigate_session(session):
                if width > 700:
                    page.get_by_role("link", name=f"Chat {session}", exact=True).click()
                else:
                    page.goto(f"{base_url}/session/{session}")

            navigate_session("b")
            expect(textbox).to_have_value("")
            expect(page.get_by_role("button", name=re.compile("选择模型与推理强度"))).to_contain_text("first-model默认")
            textbox.fill("B-local-draft")
            navigate_session("a")
            expect(textbox).to_have_value("A-local-draft")
            expect(page.get_by_role("button", name=re.compile("选择模型与推理强度"))).to_contain_text("second-model高")
            page.reload()
            expect(textbox).to_have_value("A-local-draft")
            expect(page.get_by_role("button", name=re.compile("选择模型与推理强度"))).to_contain_text("second-model高")
            page.screenshot(path=str(screenshots / f"draft-{theme}-{width}.png"))
            account["id"] = "bob"
            page.reload()
            expect(textbox).to_have_value("")
            expect(page.get_by_role("button", name=re.compile("选择模型与推理强度"))).to_contain_text("first-model默认")
            account["id"] = "alice"
            page.reload()
            expect(textbox).to_have_value("A-local-draft")
            expect(page.get_by_role("button", name=re.compile("选择模型与推理强度"))).to_contain_text("second-model高")
            assert not errors, errors
            assert not writes, writes
            results.append({"theme": theme, "width": width, "restored": True, "owner_isolated": True, "draft_api_writes": 0, "page_errors": 0})
            context.close()
        browser.close()
    (screenshots / "inspection.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5197")
    parser.add_argument("--executable", required=True)
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-composer-drafts"))
    args = parser.parse_args()
    check(args.base_url.rstrip("/"), args.executable, args.screenshots)
