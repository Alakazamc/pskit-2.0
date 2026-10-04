"""Check the rendered model picker with synthetic API data, without live model calls."""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def check(base_url: str, executable: str | None, screenshots: Path) -> None:
    screenshots.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=executable, args=["--no-sandbox"]
        )
        for theme, language, width, height in (
            ("dark", "zh", 1360, 860), ("light", "en", 1360, 860),
            ("dark", "en", 390, 860), ("light", "zh", 390, 600),
        ):
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                is_mobile=width < 500, has_touch=width < 500,
            )
            await context.add_init_script(
                f"localStorage.setItem('research_access_token', 'browser-test-only');"
                f"localStorage.setItem('pskit-theme', '{theme}');"
                f"localStorage.setItem('research_language', '{language}');"
            )
            page = await context.new_page()
            errors, sent = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def api(route):
                path = route.request.url.split("/api/v1", 1)[1].split("?")[0]
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
                              "title": "Picker check", "status": "idle"}]
                elif path.endswith("/messages") and route.request.method == "POST":
                    sent.append(route.request.post_data_json)
                    value = {"run_id": "run-browser"}
                elif path == "/models":
                    value = [
                        {"id": "anthropic/claude-opus-4-8", "supports_images": True,
                         "reasoning_levels": ["medium", "high", "xhigh", "max"]},
                        {"id": "openai/text-model", "supports_images": False,
                         "reasoning_levels": []},
                        *[{"id": f"provider/model-{n}", "supports_images": False,
                           "reasoning_levels": []} for n in range(20)],
                    ]
                elif path.endswith("/events"):
                    event = {"id": "1", "type": "run.completed", "data": {},
                             "created_at": "2026-10-04T07:00:00Z"}
                    await route.fulfill(content_type="text/event-stream",
                                        body="data: " + json.dumps(event) + "\n\n")
                    return
                await route.fulfill(status=200, content_type="application/json",
                                    body=json.dumps(value))

            await page.route("**/api/v1/**", api)
            await page.goto(base_url.rstrip("/") + "/session/session-browser")
            zh = language == "zh"
            choose = "选择模型与推理强度" if zh else "Choose model and thinking level"
            search_name = "搜索模型" if zh else "Search models"
            thinking = "推理强度" if zh else "Thinking level"
            switch_model = "切换模型" if zh else "Switch model"
            high = "高" if zh else "High"
            maximum = "最大" if zh else "Maximum"
            medium = "中" if zh else "Medium"
            default = "默认" if zh else "Default"
            trigger = page.get_by_role("button", name=choose, exact=False)
            trigger_box = await trigger.bounding_box()
            send_box = await page.locator(".send-button").bounding_box()
            add_box = await page.locator(".add-button").bounding_box()
            assert 0 <= send_box["x"] - trigger_box["x"] - trigger_box["width"] <= 12, (trigger_box, send_box)
            assert trigger_box["x"] > add_box["x"], (trigger_box, add_box)
            trigger_appearance = await trigger.evaluate("el => ({border: getComputedStyle(el).borderTopWidth, background: getComputedStyle(el).backgroundColor})")
            assert trigger_appearance["border"] == "0px", trigger_appearance
            await trigger.click()
            panel = page.locator(".composer-settings-popover")
            assert await panel.get_attribute("data-side") == "top"
            settings_box = await panel.bounding_box()
            assert settings_box["width"] * settings_box["height"] <= 22000, settings_box
            assert settings_box["height"] <= 176, settings_box
            assert await page.get_by_role("searchbox").count() == 0
            await page.get_by_role("button", name=switch_model, exact=False).click()
            search = page.get_by_role("searchbox", name=search_name)
            await search.focus()
            appearance = await search.evaluate("""el => {
                const s = getComputedStyle(el), row = getComputedStyle(el.parentElement);
                return {outline: s.outlineStyle, border: s.borderTopWidth,
                        shadow: s.boxShadow, rowBorder: row.borderTopWidth,
                        rowBackground: row.backgroundColor};
            }""")
            await page.screenshot(path=str(screenshots / f"{theme}-{language}-{width}-focus.png"))
            assert appearance["outline"] == "none", appearance
            assert appearance["border"] == "0px" and appearance["shadow"] == "none", appearance
            assert appearance["rowBorder"] == "0px", appearance
            assert appearance["rowBackground"] not in {"transparent", "rgba(0, 0, 0, 0)"}, appearance
            model_popup = await page.locator(".composer-model-popover").bounding_box()
            assert model_popup["x"] >= 0 and model_popup["x"] + model_popup["width"] <= width + 1, model_popup
            assert model_popup["y"] >= 0 and model_popup["y"] + model_popup["height"] <= height + 1, model_popup
            await page.keyboard.press("Escape")
            assert await page.get_by_role("searchbox").count() == 0

            slider = page.get_by_role("slider", name=thinking)
            assert await slider.get_attribute("aria-orientation") == "vertical"
            await slider.focus()
            await slider.press("ArrowUp")
            assert await slider.get_attribute("aria-valuetext") == medium
            await slider.press("ArrowUp")
            assert await slider.get_attribute("aria-valuetext") == high
            await slider.press("ArrowDown")
            assert await slider.get_attribute("aria-valuetext") == medium
            await slider.press("Home")
            assert await slider.get_attribute("aria-valuetext") == default
            await slider.press("End")
            assert await slider.get_attribute("aria-valuetext") == maximum
            await slider.press("Home")
            box = await slider.bounding_box()
            x, bottom, top = box["x"] + box["width"] / 2, box["y"] + box["height"] - 10, box["y"] + 10
            if width < 500:
                cdp = await context.new_cdp_session(page)
                await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": bottom}]})
                for fraction in (0.25, 0.5, 0.75, 1):
                    await cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": bottom + (top - bottom) * fraction}]})
                await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            else:
                await page.mouse.move(x, bottom)
                await page.mouse.down()
                await page.mouse.move(x, top, steps=12)
                await page.mouse.up()
            assert await slider.get_attribute("aria-valuetext") == maximum
            await slider.press("ArrowDown")
            await slider.press("ArrowDown")
            assert await slider.get_attribute("aria-valuetext") == high
            await page.screenshot(path=str(screenshots / f"{theme}-{language}-{width}-lever.png"))
            popup = await panel.bounding_box()
            assert popup["width"] <= 220, popup
            assert popup["x"] >= 0 and popup["x"] + popup["width"] <= width + 1, popup
            assert popup["y"] >= 0 and popup["y"] + popup["height"] <= height + 1, popup
            assert box["y"] + box["height"] <= popup["y"] + popup["height"], (box, popup)

            await page.get_by_role("button", name=switch_model, exact=False).click()
            await search.fill("text-model")
            await page.get_by_role("button", name="openai/text-model", exact=True).click()
            assert await page.get_by_role("slider").count() == 0
            assert await panel.is_visible()
            await page.get_by_role("button", name=switch_model, exact=False).click()
            await search.fill("opus")
            await page.get_by_role("button", name="anthropic/claude-opus-4-8", exact=True).click()
            assert await page.get_by_role("searchbox").count() == 0
            assert await slider.get_attribute("aria-valuetext") == default
            await slider.focus()
            await slider.press("ArrowUp")
            await slider.press("ArrowUp")
            await page.keyboard.press("Escape")
            assert await trigger.inner_text() == ("claude-opus-4-8\n" + high)
            await page.get_by_role("textbox", name="消息内容" if zh else "Message", exact=True).fill("Explain the results")
            await page.get_by_role("button", name="发送消息" if zh else "Send message", exact=True).click()
            await page.wait_for_function("document.querySelector('.composer-input-area textarea').value === ''")
            assert sent[0]["model"] == "anthropic/claude-opus-4-8" and sent[0]["reasoning_effort"] == "high", sent
            assert not errors, errors
            print(f"PASS {theme} {language} {width}x{height}: right controls, settings panel, focus, upward drag, keyboard, reset, request", flush=True)
            await context.close()
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5174")
    parser.add_argument("--browser-executable")
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-model-picker-check"))
    args = parser.parse_args()
    asyncio.run(check(args.base_url, args.browser_executable, args.screenshots))
