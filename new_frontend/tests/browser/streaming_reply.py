"""Check reply loading position and streaming growth with controlled, synthetic SSE."""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect

STREAM = r"""
const originalFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  if (/\/runs\/run-browser-\d+\/events/.test(String(input))) {
    window.browserStreamUrl = String(input);
    let sequence = 0;
    const encoder = new TextEncoder();
    return new Response(new ReadableStream({start(controller) {
      window.emitBrowserEvent = (type, data) => controller.enqueue(encoder.encode(
        'data: ' + JSON.stringify({id: String(++sequence), type, data,
          created_at: '2026-10-04T08:00:00Z'}) + '\n\n'));
    }}), {headers: {'content-type': 'text/event-stream'}});
  }
  const response = await originalFetch(input, init);
  if (/\/runs\/run-browser-\d+$/.test(String(input)) && init?.method === 'DELETE') {
    window.emitBrowserEvent('run.cancelled', {});
  }
  return response;
};
"""


async def check(base_url: str, executable: str | None, screenshots: Path) -> None:
    screenshots.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        for theme, language, width, height in (
            ("dark", "zh", 1360, 860), ("light", "en", 1360, 860),
            ("dark", "en", 390, 860), ("light", "zh", 390, 600),
        ):
            context = await browser.new_context(viewport={"width": width, "height": height},
                                                is_mobile=width < 500, has_touch=width < 500)
            await context.add_init_script(STREAM +
                f"localStorage.setItem('research_access_token', 'browser-test-only');"
                f"localStorage.setItem('pskit-theme', '{theme}');"
                f"localStorage.setItem('research_language', '{language}');")
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            state = {"run": 0, "status": "idle", "text": ""}
            identity = {"id": "browser-user", "name": "Browser test", "is_anonymous": False,
                        "email": "browser@example.invalid"}
            history = [{"id": "user-old", "role": "user", "parts": [{"type": "text", "text": "Earlier question"}],
                        "created_at": "2026-10-04T07:00:00Z"},
                       {"id": "assistant-old", "role": "assistant", "parts": [{"type": "text", "text":
                        "\n\n".join(f"### Historical result {n}\nRead this earlier paragraph." for n in range(12))}],
                        "created_at": "2026-10-04T07:01:00Z"}]

            async def api(route):
                path = route.request.url.split("/api/v1", 1)[1].split("?")[0]
                value = []
                if path == "/auth/refresh":
                    value = {"access_token": "browser-test-only", "expires_in": 3600, "user": identity}
                elif path == "/me":
                    value = identity
                elif path == "/g":
                    value = [{"id": "project-browser-user", "name": "Personal", "description": ""}]
                elif path == "/c":
                    value = [{"id": "session-browser", "project_id": "project-browser-user", "title": "Streaming check",
                              "status": state["status"], "latest_run_id": f'run-browser-{state["run"]}' if state["run"] else None}]
                elif path.endswith("/messages") and route.request.method == "POST":
                    state["run"] += 1
                    state["status"] = "running"
                    history.append({"id": f'user-{state["run"]}', "role": "user", "created_at": "2026-10-04T08:00:00Z",
                                    "parts": [{"type": "text", "text": route.request.post_data_json["content"]}]})
                    value = {"run_id": f'run-browser-{state["run"]}'}
                elif path.endswith("/messages"):
                    value = history
                elif route.request.method == "DELETE":
                    state["status"] = "cancelled"
                    value = {"status": "cancelled"}
                elif path == "/models":
                    value = [{"id": "text-model", "supports_images": False, "reasoning_levels": []}]
                await route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

            await page.route("**/api/v1/**", api)
            await page.goto(base_url.rstrip("/") + "/session/session-browser")
            zh = language == "zh"
            send = page.get_by_role("button", name="发送消息" if zh else "Send message", exact=True)
            stop = page.get_by_role("button", name="取消运行" if zh else "Cancel run", exact=True)
            generating = page.get_by_role("status", name="正在生成回复" if zh else "Generating reply", exact=True)
            textarea = page.get_by_role("textbox", name="消息内容" if zh else "Message", exact=True)
            await textarea.fill("Explain Redis")
            await send.click()
            await expect(stop).to_be_enabled()
            await expect(generating).to_have_count(1)
            await page.wait_for_function("window.browserStreamUrl?.includes('run-browser-1/events')")

            async def position():
                geometry = await generating.evaluate("""el => {
                    const body = el.closest('.message-body'), header = body.firstElementChild;
                    const b = body.getBoundingClientRect(), h = header.getBoundingClientRect(), m = el.getBoundingClientRect();
                    return {first: header.contains(el), actions: !!el.closest('.message-actions'),
                            x: m.left-b.left, y: h.top-b.top, width: m.width, height: h.height};
                }""")
                assert geometry == {"first": True, "actions": False, "x": 0, "y": 0, "width": 28, "height": 28}, geometry
                assert await page.locator(".mono-run-controls").count() == 0
                return geometry

            initial = await position()
            await page.screenshot(path=str(screenshots / f"{theme}-{language}-{width}-waiting.png"))

            async def delta(text):
                state["text"] += text
                await page.evaluate("text => window.emitBrowserEvent('message.delta', {delta: text})", text)
                await page.wait_for_function("text => document.querySelector('.message-row.assistant:last-child .message-markdown')?.textContent.includes(text)",
                                             arg=text.split("\n")[-1], timeout=10000)

            await delta("## Redis\n\nRedis stores data in memory.")
            assert await position() == initial
            await page.screenshot(path=str(screenshots / f"{theme}-{language}-{width}-stream-start.png"))
            for n in range(8):
                await delta(f"\n\n### Section {n}\nStreamed content expands the reply. 第 {n} 段内容逐步到达。")
            await delta("\n\n```python\n" + "\n".join(f"print('row {n}')" for n in range(18)) + "\n```\n\nCode ready.")
            bottom = "(() => {const el = document.querySelector('.conversation-scroll'); return el.scrollHeight-el.clientHeight-el.scrollTop < 5;})()"
            await page.wait_for_function(bottom)
            assert await position() == initial
            assert await page.locator(".message-actions [role=status]").count() == 0
            await page.screenshot(path=str(screenshots / f"{theme}-{language}-{width}-long-stream.png"))

            scroller = page.locator(".conversation-scroll")
            await scroller.hover()
            await page.mouse.wheel(0, -10000)
            await page.wait_for_function("document.querySelector('.conversation-scroll').scrollTop < 5")
            before = await scroller.evaluate("el => el.scrollTop")
            await delta("\n\nMore content arrives while reading history.")
            assert abs(await scroller.evaluate("el => el.scrollTop") - before) < 5
            await scroller.evaluate("el => el.scrollTop = el.scrollHeight")
            await delta("\n\nFinal streamed paragraph.")
            await page.wait_for_function(bottom)
            header_size = await page.locator(".message-row.assistant:last-child .assistant-response-header").bounding_box()

            state["status"] = "completed"
            history.append({"id": "assistant-completed", "role": "assistant", "created_at": "2026-10-04T08:00:00Z",
                            "parts": [{"type": "text", "text": state["text"]}]})
            await page.evaluate("window.emitBrowserEvent('run.completed', {status: 'completed'})")
            await expect(generating).to_have_count(0)
            await expect(send).to_be_visible()
            await expect(page.locator(".message-row.assistant")).to_have_count(2)
            await expect(page.locator(".message-row.assistant:last-child .message-markdown")).to_contain_text("Final streamed paragraph.")
            finished_size = await page.locator(".message-row.assistant:last-child .assistant-response-header").bounding_box()
            assert finished_size["height"] == header_size["height"] == 28
            assert await page.locator(".assistant-response-header .message-spinner").count() == 0

            await textarea.fill("Another question")
            await send.click()
            await expect(generating).to_have_count(1)
            await expect(stop).to_be_enabled()
            await page.wait_for_function("window.browserStreamUrl?.includes('run-browser-2/events')")
            await stop.click()
            await expect(generating).to_have_count(0)
            await expect(send).to_be_visible()
            await page.screenshot(path=str(screenshots / f"{theme}-{language}-{width}-cancelled.png"))
            assert not errors, errors
            print(f"PASS {theme} {language} {width}x{height}: top-left loading, first-event wait, Markdown growth, autoscroll, completion, stop", flush=True)
            await context.close()
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--browser-executable")
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-streaming-reply-check"))
    args = parser.parse_args()
    asyncio.run(check(args.base_url, args.browser_executable, args.screenshots))
