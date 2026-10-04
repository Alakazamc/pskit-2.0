"""Observe deferred conversation titles through browser HTTP boundaries."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def check(base_url: str, executable: str, screenshots: Path) -> None:
    screenshots.mkdir(parents=True, exist_ok=True)
    observations = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        for theme, width, language, project in [
            ("dark", 1360, "zh", False), ("light", 1360, "en", True),
            ("dark", 390, "en", True), ("light", 390, "zh", False),
        ]:
            mobile, zh = width < 700, language == "zh"
            context = browser.new_context(viewport={"width": width, "height": 900}, has_touch=mobile)
            context.add_init_script("localStorage.setItem('research_access_token','browser-test-only');" + f"localStorage.setItem('research_language','{language}');localStorage.setItem('pskit-theme','{theme}');")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            state = {"created": False, "sent": False, "named": False, "reads": 0}
            initial_title = "帮我分析蛋白质结构" if zh else "Help me analyze protein structure"
            generated_title = "蛋白质结构分析" if zh else "Protein structure analysis"
            target = "/api/v1/g/g-p-lab/c" if project else "/api/v1/c"
            posts = []

            def session():
                return {
                    "id": "session-first", "project_id": "project-lab" if project else "project-alice",
                    "title": generated_title if state["named"] else initial_title,
                    "title_status": "generated" if state["named"] else "pending" if state["sent"] else "idle",
                    "status": "completed" if state["sent"] else "idle",
                    "latest_run_id": "run-first" if state["sent"] else None,
                }

            def api(route):
                request = route.request
                path = urlparse(request.url).path
                if request.method == "POST":
                    posts.append((path, request.post_data_json))
                if path.endswith("/me"):
                    data = {"id": "alice", "name": "Alice", "email": "alice@example.org"}
                elif path.endswith("/g"):
                    data = [{"id": "project-alice", "name": "Personal", "description": ""}, {"id": "project-lab", "name": "Lab", "description": ""}]
                elif path.endswith("/models"):
                    data = [{"id": "anthropic/claude-opus-4-8", "supports_images": True, "reasoning_levels": ["medium", "high"]}]
                elif path == target:
                    if request.method == "POST":
                        assert request.post_data_json["auto_title"] is True
                        state["created"] = True
                        data = session()
                    else:
                        state["reads"] += 1
                        data = [session()] if state["created"] else []
                elif path.endswith("/c/session-first/messages"):
                    if request.method == "POST":
                        state["sent"] = True
                        data = {"run_id": "run-first"}
                    else:
                        data = [{"id": "user-first", "session_id": "session-first", "role": "user", "parts": [{"type": "text", "text": initial_title}], "created_at": "2026-10-05T00:00:00Z"}, {"id": "assistant-first", "session_id": "session-first", "role": "assistant", "parts": [{"type": "text", "text": "Completed analysis"}], "created_at": "2026-10-05T00:00:01Z"}] if state["sent"] else []
                elif "/runs/run-first/events" in path:
                    event = {"id": "1", "run_id": "run-first", "type": "run.completed", "data": {}}
                    route.fulfill(status=200, content_type="text/event-stream", body=f"data: {json.dumps(event)}\n\n")
                    return
                elif path.endswith("/c/session-first"):
                    data = session()
                else:
                    data = []
                route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

            page.route("**/api/v1/**", api)
            page.goto(base_url + ("/p/project-lab/new" if project else "/"))
            composer = page.get_by_role("textbox", name="消息内容" if zh else "Message", exact=True)
            composer.fill(initial_title)
            model = page.locator(".composer-model-trigger")
            expect(model).to_contain_text("claude-opus-4-8")
            selected_model = model.text_content()
            page.get_by_role("button", name="发送消息" if zh else "Send message", exact=True).click()
            expect(page.get_by_text("Completed analysis", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="停止生成" if zh else "Stop generation", exact=True)).to_have_count(0)
            expect(page.locator(".mono-topbar small")).to_have_text(initial_title)
            composer.fill("Next round draft")
            state["named"] = True
            expect(page.locator(".mono-topbar small")).to_have_text(generated_title)
            expect(composer).to_have_value("Next round draft")
            expect(model).to_have_text(selected_model)
            expect(page).to_have_url(base_url + ("/p/project-lab/c/session-first" if project else "/session/session-first"))
            if mobile:
                page.get_by_role("button", name="打开侧栏" if zh else "Open sidebar", exact=True).click()
                sidebar = page.locator(".mono-mobile-overlay")
            else:
                sidebar = page.locator(".mono-desktop-sidebar")
            expect(sidebar.get_by_role("link", name=generated_title, exact=True)).to_be_visible()
            reads = state["reads"]
            page.wait_for_timeout(1200)
            assert state["reads"] == reads, state
            assert len(posts) == 2, posts
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            page.screenshot(path=str(screenshots / f"title-{theme}-{width}.png"))
            observations.append({"theme": theme, "width": width, "language": language, "project": project, "title": generated_title, "draft_retained": True, "extra_posts": len(posts) - 2, "polling_stopped": True, "page_errors": errors})
            context.close()
        browser.close()
    (screenshots / "inspection.json").write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(observations, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5197")
    parser.add_argument("--executable", required=True)
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-session-titles"))
    args = parser.parse_args()
    check(args.base_url.rstrip("/"), args.executable, args.screenshots)
