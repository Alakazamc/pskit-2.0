"""Check per-turn attachment limits against a production build with synthetic APIs."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright, expect


async def check(base_url: str, screenshots: Path) -> None:
    screenshots.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox"])
        for theme, language, width, height in (
            ("dark", "zh", 1360, 860), ("light", "en", 1360, 860),
            ("dark", "en", 390, 860), ("light", "zh", 390, 600),
        ):
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                is_mobile=width < 500, has_touch=width < 500,
            )
            await context.add_init_script(
                "localStorage.setItem('research_access_token', 'browser-test-only');"
                f"localStorage.setItem('pskit-theme', '{theme}');"
                f"localStorage.setItem('research_language', '{language}');"
            )
            page = await context.new_page()
            uploads, sent, errors = [], [], []
            page.on("pageerror", lambda error, *, errors=errors: errors.append(str(error)))

            async def api(route, _request, *, uploads=uploads, sent=sent):
                url = urlsplit(route.request.url)
                path = url.path.split("/api/v1", 1)[1]
                identity = {"id": "browser-user", "name": "Browser test",
                            "email": "browser@example.invalid", "is_anonymous": False}
                value = []
                if path == "/auth/refresh":
                    value = {"access_token": "browser-test-only", "expires_in": 3600,
                             "user": identity}
                elif path == "/me":
                    value = identity
                elif path == "/g":
                    value = [{"id": "project-browser-user", "name": "Personal",
                              "description": ""}]
                elif path == "/c":
                    value = [{"id": "session-browser", "project_id": "project-browser-user",
                              "title": "Attachment check", "status": "idle"}]
                elif path == "/models":
                    value = [{"id": "vision-model", "supports_images": True}]
                elif path == "/files/content":
                    name = parse_qs(url.query)["name"][0]
                    uploads.append(name)
                    value = {"id": name, "name": name, "status": "ready", "size": 5}
                elif path.endswith("/messages") and route.request.method == "POST":
                    sent.append(route.request.post_data_json)
                    value = {"run_id": "run-browser"}
                elif path == "/runs/run-browser":
                    value = {"run_id": "run-browser", "status": "completed"}
                elif path.endswith("/events"):
                    event = {"id": "1", "run_id": "run-browser", "type": "run.completed",
                             "data": {}, "created_at": "2026-10-04T12:00:00Z"}
                    await route.fulfill(content_type="text/event-stream",
                                        body="data: " + json.dumps(event) + "\n\n")
                    return
                await route.fulfill(status=200, content_type="application/json",
                                    body=json.dumps(value))

            await page.route("**/api/v1/**", api)
            await page.goto(base_url.rstrip("/") + "/session/session-browser")
            composer = page.locator(".composer-wrap")
            input_files = composer.locator('input[type="file"]')
            await expect(composer).to_be_visible()
            await expect(page.get_by_role("alert")).to_have_count(0)
            files = [{"name": f"notes-{index + 1}.txt", "mimeType": "text/plain",
                      "buffer": b"notes"} for index in range(11)]
            await input_files.set_input_files(files[:10])
            await expect(composer.get_by_role("group")).to_have_count(10)
            await expect(composer.get_by_role("progressbar")).to_have_count(0)
            await expect(page.get_by_role("alert")).to_have_count(0)
            await expect(page.get_by_text("文本最多 1 MiB", exact=False)).to_have_count(0)
            await expect(page.get_by_text("Text files up to 1 MiB", exact=False)).to_have_count(0)

            await input_files.set_input_files(files[10])
            warning = ("每轮对话最多只能上传 10 个文件，请在下一轮上传其余文件。"
                       if language == "zh" else
                       "Upload at most 10 files per turn. Upload the remaining files in your next turn.")
            await expect(page.get_by_role("alert")).to_have_text(warning)
            assert sorted(uploads) == sorted(file["name"] for file in files[:10]), uploads
            send = composer.get_by_role("button", name="发送消息" if language == "zh" else "Send message")
            send_box = await send.bounding_box()
            assert send_box and 0 <= send_box["y"] and send_box["y"] + send_box["height"] <= height, send_box
            previews = composer.get_by_role("region", name="附件" if language == "zh" else "Attachments")
            await previews.focus()
            await previews.press("End")
            await expect(previews.get_by_role("group").last).to_be_in_viewport(ratio=1)
            scroll = await previews.evaluate("el => ({top: el.scrollTop, height: el.clientHeight, total: el.scrollHeight})")
            assert scroll["total"] <= scroll["height"] or scroll["top"] > 0, scroll
            await page.screenshot(path=str(screenshots / f"{theme}-{language}-{width}-overflow.png"))

            await composer.get_by_role("textbox", name="消息内容" if language == "zh" else "Message", exact=True).fill("Review these files")
            await send.click()
            await expect(composer.get_by_role("group")).to_have_count(0)
            await expect(page.get_by_role("alert")).to_have_count(0)
            assert len(sent) == 1 and len(sent[0]["attachments"]) == 10, sent
            await input_files.set_input_files(files[10])
            await expect(composer.get_by_role("group")).to_have_count(1)
            await expect(composer.get_by_role("progressbar")).to_have_count(0)
            await expect(page.get_by_role("alert")).to_have_count(0)
            assert uploads[-1] == "notes-11.txt" and len(uploads) == 11, uploads
            assert not errors, errors
            print(f"PASS {theme} {language} {width}x{height}: no notice, ten files, overflow, next turn", flush=True)
            await context.close()
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-attachment-limit-check"))
    args = parser.parse_args()
    asyncio.run(check(args.base_url, args.screenshots))
