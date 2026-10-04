"""Rendered admin acceptance using complete synthetic backend-contract HTTP responses.

No live service, worker, model, or paid calls. Run against the local Vite server.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "new_backend"))
from app.contracts.admin import (
    AdminJob,
    AdminMe,
    AdminModel,
    AdminService,
    AdminUser,
    AuditEvent,
    ModelDraft,
)
from app.contracts.sandbox import SandboxSummary


def fixtures():
    principal = AdminMe(
        user_id="browser-user",
        roles=["platform_admin"],
        permissions=[
            "models:read",
            "models:write",
            "models:publish",
            "services:read",
            "services:write",
            "services:publish",
            "quotas:read",
            "quotas:write",
            "jobs:read",
            "jobs:cancel",
            "sandboxes:read",
            "sandboxes:drain",
            "usage:read",
            "usage:reconcile",
            "audit:read",
        ],
        service_ids=[],
    )
    policy = ModelDraft(
        expected_revision=0,
        reason="Initial synthetic policy",
        allowed_user_ids=["browser-user"],
    )
    model = AdminModel(
        id="lab-chat",
        revision=1,
        state="draft",
        gateway_available=True,
        gateway={
            "id": "lab-chat",
            "supports_images": True,
            "reasoning_levels": ["low", "high"],
        },
        draft=policy,
    )
    capability = {
        "id": "rna.predict",
        "version": "1",
        "input_schema": {
            "type": "object",
            "properties": {
                f"sequence_{index}": {"type": "string"} for index in range(50)
            },
        },
        "required_usage": ["gpu_device_ms"],
        "accepted_sources": ["service_reported"],
        "visibility": "draft",
        "allowed_users": ["browser-user"],
        "gpu_count": 1,
        "max_budget": {"cpu_core_ms": 0, "gpu_device_ms": 60000},
        "concurrency": 1,
        "max_execution_seconds": 1800,
        "cancellation": "cooperative",
        "limit_mode": "soft",
        "exclusive_process": False,
    }
    service = AdminService(
        service_id="rna",
        revision=1,
        state="validated",
        name="RNA",
        owner_user_id="browser-user",
        transport="worker_pull",
        endpoint_ref="lab/rna",
        credential_ref="service/rna",
        model_version="weights-v1",
        capabilities=[capability],
        schema_digest="sha256:synthetic",
    )
    user = AdminUser(
        user_id="alice",
        revision=2,
        tier="member",
        token_monthly_limit=100000,
        gpu_daily_minutes=60,
        cpu_daily_core_ms=60000,
        concurrency_limit=2,
        storage_limit_bytes=None,
        tokens={"limit": 100000, "used": 900, "reserved": 100, "remaining": 99000},
        gpu={"limit": 60, "used": 12, "reserved": 8, "remaining": 40},
        cpu={"limit": 60000, "used": 12000, "reserved": 18000, "remaining": 30000},
    )
    job = AdminJob(
        job_id="compute-1",
        user_id="alice",
        service_id="rna",
        capability_id="rna.predict",
        status="running",
        accounting_status="reserved",
        progress=40,
        revision=1,
        cancellation_state="none",
    )
    sandbox = SandboxSummary(
        owner_id="alice",
        instance_id="container-1",
        volume_id="volume-1",
        image_digest="sha256:synthetic-approved",
        state="ready",
        runtime_state="running",
        active_sessions=["session-1"],
        revision=1,
        last_completed_at=1791043200,
    )
    audit = AuditEvent(
        event_id="audit-1",
        actor_user_id="browser-user",
        action="quota.updated",
        resource_id="alice",
        reason="Synthetic resource policy change",
        request_id="request-1",
        created_at="2026-10-04T10:00:00Z",
        before={"gpu_daily_minutes": 60},
        after={"gpu_daily_minutes": 30},
    )
    dump = lambda item: item.model_dump(mode="json")
    return dump(principal), {
        "llm-aliases": [dump(model)],
        "services": [dump(service)],
        "users": [
            {**dump(user), "user_id": "alice" if index == 0 else f"synthetic-{index}"}
            for index in range(75)
        ],
        "jobs": [dump(job)],
        "sandboxes": [dump(sandbox)],
        "usage/reconciliation": [
            {**dump(job), "accounting_status": "pending_reconciliation"}
        ],
        "audit-events": [dump(audit)],
    }


async def check(base_url: str, executable: str | None, output: Path, case: str | None):
    output.mkdir(parents=True, exist_ok=True)
    principal, rows = fixtures()
    checks = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=executable, args=["--no-sandbox"]
        )
        for theme in ("dark", "light"):
            for language in ("zh", "en"):
                for width in (1360, 390):
                    suffix = f"{theme}-{language}-{width}"
                    if case and case != suffix:
                        continue
                    context = await browser.new_context(
                        viewport={"width": width, "height": 860},
                        is_mobile=width == 390,
                        has_touch=width == 390,
                    )
                    await context.add_init_script(
                        f"localStorage.setItem('research_access_token', 'browser-test-only');"
                        f"localStorage.setItem('pskit-theme', '{theme}');"
                        f"localStorage.setItem('research_language', '{language}');"
                    )
                    page = await context.new_page()
                    errors, mutations = [], []
                    page.on(
                        "pageerror",
                        lambda error, errors=errors: errors.append(str(error)),
                    )

                    async def api(route, mutations=mutations):
                        path = route.request.url.split("/api/v1", 1)[1].split("?")[0]
                        identity = {
                            "id": "browser-user",
                            "name": "Browser test",
                            "email": "browser@example.invalid",
                            "is_anonymous": False,
                        }
                        if path == "/auth/refresh":
                            await route.fulfill(
                                status=200,
                                content_type="application/json",
                                body=json.dumps(
                                    {
                                        "access_token": "browser-test-only",
                                        "expires_in": 3600,
                                        "user": identity,
                                    }
                                ),
                            )
                            return
                        assert (
                            route.request.headers.get("authorization")
                            == "Bearer browser-test-only"
                        )
                        assert "x-admin-key" not in route.request.headers
                        if route.request.method != "GET":
                            mutations.append(route.request.url)
                            await route.fulfill(
                                status=422,
                                content_type="application/json",
                                body=json.dumps(
                                    {"detail": {"code": "BROWSER_READ_ONLY"}}
                                ),
                            )
                            return
                        if path == "/me":
                            value = identity
                        elif path == "/admin/me":
                            value = principal
                        elif path.startswith("/admin/"):
                            value = {
                                "items": rows.get(path.removeprefix("/admin/"), []),
                                "next_cursor": None,
                            }
                        else:
                            value = []
                        await route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(value),
                        )

                    await page.route("**/api/v1/**", api)
                    zh = language == "zh"
                    for section, title, select, field in (
                        (
                            "models",
                            "语言模型" if zh else "Language models",
                            "编辑 lab-chat" if zh else "Edit lab-chat",
                            "允许的用户 ID" if zh else "Allowed users",
                        ),
                        (
                            "services",
                            "科研服务" if zh else "Scientific services",
                            "编辑 RNA" if zh else "Edit RNA",
                            "能力定义 JSON" if zh else "Capability definitions JSON",
                        ),
                        (
                            "users",
                            "用户额度" if zh else "User quotas",
                            "编辑 alice" if zh else "Edit alice",
                            "CPU 每日核秒" if zh else "Daily CPU core seconds",
                        ),
                        (
                            "jobs",
                            "作业" if zh else "Jobs",
                            "取消 compute-1" if zh else "Cancel compute-1",
                            "变更原因" if zh else "Change reason",
                        ),
                        (
                            "sandboxes",
                            "沙箱" if zh else "Sandboxes",
                            "排空 alice" if zh else "Drain alice",
                            "变更原因" if zh else "Change reason",
                        ),
                        (
                            "usage",
                            "计量对账" if zh else "Usage reconciliation",
                            "对账 compute-1" if zh else "Reconcile compute-1",
                            "停止证据" if zh else "Stop evidence",
                        ),
                        ("audit", "审计记录" if zh else "Audit events", None, None),
                    ):
                        await page.goto(base_url.rstrip("/") + "/admin/" + section)
                        await page.get_by_role(
                            "heading", name=title, exact=True
                        ).wait_for()
                        if select:
                            action = page.get_by_role("button", name=select, exact=True)
                            await action.wait_for()
                            await action.focus()
                            await page.keyboard.press("Enter")
                            control = page.get_by_role(
                                "spinbutton" if section == "users" else "textbox",
                                name=field,
                                exact=True,
                            )
                            try:
                                await control.wait_for(timeout=5000)
                            except Exception:
                                print(
                                    {
                                        "configuration": suffix,
                                        "section": section,
                                        "errors": errors,
                                        "body": (
                                            await page.locator("body").inner_text()
                                        )[:2500],
                                    },
                                    flush=True,
                                )
                                print(
                                    await page.locator(".admin-editor").aria_snapshot(),
                                    flush=True,
                                )
                                await page.screenshot(
                                    path=str(output / f"failure-{suffix}-{section}.png")
                                )
                                raise
                            await control.focus()
                            await page.keyboard.press("Tab")
                            await page.keyboard.press("Shift+Tab")
                            assert await control.evaluate(
                                "element => element === document.activeElement"
                            )
                            frame = await page.evaluate(
                                "({scroll:document.querySelector('.admin-app').scrollTop, headerTop:document.querySelector('.admin-header').getBoundingClientRect().top})"
                            )
                            assert frame["scroll"] == 0 and frame["headerTop"] >= 0, (
                                suffix,
                                section,
                                frame,
                            )
                            style = await control.evaluate(
                                "element => { const s = getComputedStyle(element); const app = getComputedStyle(element.closest('.mono-app')); return {background:s.backgroundColor,border:s.borderColor,outline:s.outlineColor,outlineStyle:s.outlineStyle,shadow:s.boxShadow,fieldBackground:getComputedStyle(element.closest('label')).backgroundColor,expected:app.getPropertyValue('--mono-subtle').trim(),focusExpected:app.getPropertyValue('--mono-selected').trim(),scrollHeight:element.scrollHeight,clientHeight:element.clientHeight}; }"
                            )
                            assert style["outlineStyle"] == "none", (
                                suffix,
                                section,
                                style,
                            )
                            assert style["fieldBackground"] != "rgba(0, 0, 0, 0)", (
                                suffix,
                                section,
                                style,
                            )
                            for key in ("border",):
                                rgb = [
                                    int(value)
                                    for value in re.findall(r"\d+", style[key])[:3]
                                ]
                                assert not all(value > 235 for value in rgb), (
                                    suffix,
                                    section,
                                    style,
                                )
                            assert style["shadow"] == "none", (suffix, section, style)
                            if section == "services":
                                assert style["scrollHeight"] > style["clientHeight"], (
                                    style
                                )
                            checks.append(
                                {
                                    "configuration": suffix,
                                    "page": section,
                                    "focus": style,
                                }
                            )
                        else:
                            await page.get_by_role(
                                "rowheader", name=re.compile(r"^quota.updated")
                            ).wait_for()
                            await page.locator(".admin-table-scroll").focus()
                            await page.keyboard.press("ArrowRight")
                            await page.locator("details summary").focus()
                            assert await page.locator("details summary").evaluate(
                                "element => element === document.activeElement"
                            )
                            await page.keyboard.press("Space")
                            assert await page.locator("details").evaluate(
                                "element => element.open"
                            )
                            assert await page.locator("details dd").first.evaluate(
                                "element => element.clientWidth >= 80"
                            )
                            await page.locator(".admin-table-scroll").focus()
                            for _ in range(8):
                                await page.keyboard.press("ArrowRight")
                            try:
                                await page.get_by_text(
                                    "request-1", exact=True
                                ).wait_for(timeout=2000)
                            except Exception:
                                print(
                                    await page.locator("details").evaluate(
                                        "element => ({open:element.open,focus:document.activeElement?.tagName,rect:element.getBoundingClientRect().toJSON(),html:element.outerHTML})"
                                    ),
                                    flush=True,
                                )
                                await page.screenshot(
                                    path=str(output / f"failure-{suffix}-audit.png")
                                )
                                raise
                        overflow = await page.evaluate(
                            "({width:innerWidth, page:document.documentElement.scrollWidth, body:document.body.scrollWidth})"
                        )
                        assert overflow["page"] <= width, (suffix, section, overflow)
                        assert overflow["body"] <= width, (suffix, section, overflow)
                        assert await page.locator("h1").count() == 1
                        await page.screenshot(
                            path=str(output / f"{suffix}-{section}.png")
                        )
                    assert not errors, (suffix, errors)
                    assert not mutations, (suffix, mutations)
                    await context.close()
                    print(
                        f"PASS {suffix}: 7 routes, keyboard, input focus, scrollbars and bounded layout",
                        flush=True,
                    )
        await browser.close()
    (output / "checks.json").write_text(json.dumps(checks, indent=2))
    print(
        f"PASS {len(checks) // 6 * 7} rendered pages; screenshots and focus evidence: {output}",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5195")
    parser.add_argument("--executable")
    parser.add_argument("--output", type=Path, default=Path("/tmp/pskit-admin-browser"))
    parser.add_argument(
        "--case", help="One configuration, e.g. dark-zh-390; default runs all eight"
    )
    args = parser.parse_args()
    asyncio.run(check(args.base_url, args.executable, args.output, args.case))
