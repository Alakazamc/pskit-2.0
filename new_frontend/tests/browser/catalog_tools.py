"""Inspect shared catalogs and tool details in Chromium with synthetic HTTP only."""

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def check(base_url: str, executable: str, screenshots: Path) -> None:
    screenshots.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        for theme, width in [("dark", 1360), ("light", 1360), ("dark", 390), ("light", 390)]:
            context = browser.new_context(viewport={"width": width, "height": 850})
            context.add_init_script(
                "localStorage.setItem('research_access_token','browser-test-only');"
                "localStorage.setItem('research_language','en');"
                f"localStorage.setItem('pskit-theme','{theme}');"
            )
            page = context.new_page()
            errors, histories, invocations = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def api(route):
                request = route.request
                parsed = urlparse(request.url)
                path = parsed.path
                if path.endswith("/me"):
                    data = {"id": "alice", "name": "Alice", "email": "alice@example.org"}
                elif path.endswith("/g"):
                    data = [{"id": "project-alice", "name": "Personal", "description": ""},
                            {"id": "study", "name": "RNA Study", "description": "Candidate analysis"}]
                elif path.endswith("/mcp/tools"):
                    data = [
                        {"name": "fetch_uniprot", "description": "Fetch a protein record from UniProt.", "input_schema": {"properties": {"accession": {"type": "string"}}, "required": ["accession"]}},
                        {"name": "search_pdb", "description": "Search protein structures", "input_schema": {"properties": {"query": {"type": "string"}}}},
                    ]
                elif path.endswith("/fetch_uniprot/invoke"):
                    invocations.append(request.post_data_json)
                    data = {"tool": "fetch_uniprot", "status": "completed", "result": {"accession": "P12345"}}
                elif path.endswith("/tool-runs"):
                    tool = parse_qs(parsed.query).get("tool", ["all"])[0]
                    histories.append(tool)
                    data = [{"id": tool, "tool": tool, "title": f"{tool} saved call", "arguments": {}, "result": {"evidence": "persisted"}, "created_at": "2026-10-05T00:00:00Z"}]
                elif path.endswith("/skills"):
                    data = [{"id": "rna-review", "name": "RNA review", "description": "Review papers and experimental evidence", "version": 3},
                            {"id": "structure", "name": "Structure analysis", "description": "Inspect molecular structures", "version": 1}]
                elif path.endswith("/resources"):
                    data = [{"id": "pubmed", "name": "PubMed", "description": "Biomedical literature"}]
                elif path.endswith("/artifacts"):
                    data = [{"id": "report", "name": "report.md", "kind": "text", "available": True}]
                elif path.endswith("/report/preview"):
                    data = {"id": "report", "text": "# Analysis result", "truncated": False}
                else:
                    data = []
                route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

            page.route("**/api/v1/**", api)
            page.goto(f"{base_url}/tools")
            card = page.get_by_role("link", name="fetch_uniprot", exact=True)
            expect(card).to_be_visible()
            bounds = card.bounding_box()
            assert bounds and bounds["width"] == 100 and 79 <= bounds["height"] <= 84, bounds
            search = page.get_by_role("searchbox", name="Search tools")
            search.fill("protein record")
            expect(page.get_by_role("link", name="search_pdb", exact=True)).to_have_count(0)
            focus = search.evaluate("e => { const s=getComputedStyle(e); return {border:s.borderWidth, outline:s.outlineWidth, shadow:s.boxShadow}; }")
            assert focus == {"border": "0px", "outline": "0px", "shadow": "none"}, focus
            search.fill("")
            card.focus()
            outline = card.evaluate("e => getComputedStyle(e).outlineWidth")
            assert outline != "0px", outline
            page.screenshot(path=str(screenshots / f"tools-{theme}-{width}.png"))
            card.click()
            panel = page.get_by_role("dialog", name="fetch_uniprot", exact=True)
            expect(panel).to_be_visible()
            assert not histories, histories
            panel.get_by_role("textbox", name="accession").fill("P12345")
            panel.get_by_role("button", name="Run tool", exact=True).click()
            expect(panel.get_by_text('"accession": "P12345"', exact=False)).to_be_visible()
            assert invocations == [{"accession": "P12345"}], invocations
            panel.get_by_role("tab", name="Run history").click()
            expect(panel.get_by_text("fetch_uniprot saved call", exact=True)).to_be_visible()
            panel.get_by_role("button", name="Edit", exact=True).click()
            field = panel.get_by_role("textbox", name="accession")
            expect(field).to_have_value("P12345")
            expect(field).to_be_focused()
            panel_bounds = panel.bounding_box()
            assert panel_bounds and panel_bounds["x"] >= 0 and panel_bounds["x"] + panel_bounds["width"] <= width, panel_bounds
            page.screenshot(path=str(screenshots / f"tool-detail-{theme}-{width}.png"))
            page.keyboard.press("Escape")
            expect(card).to_be_focused()
            page.get_by_role("link", name="search_pdb", exact=True).click()
            pdb = page.get_by_role("dialog", name="search_pdb", exact=True)
            pdb.get_by_role("tab", name="Run history").click()
            expect(pdb.get_by_text("search_pdb saved call", exact=True)).to_be_visible()
            expect(pdb.get_by_text("fetch_uniprot saved call", exact=True)).to_have_count(0)
            assert histories == ["fetch_uniprot", "search_pdb"], histories
            page.keyboard.press("Escape")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_role("link", name="Structure viewer", exact=True).click()
            viewer = page.get_by_role("dialog", name="Structure viewer", exact=True)
            expect(viewer.get_by_role("textbox", name="PDB ID")).to_be_visible()
            assert viewer.evaluate("e => e.scrollWidth <= e.clientWidth")
            page.keyboard.press("Escape")

            for route_path, name, role in [("skills", "RNA review", "button"), ("resources", "PubMed", "button"), ("g", "RNA Study", "link"), ("artifacts", "report.md", "button")]:
                page.goto(f"{base_url}/{route_path}")
                entry = page.locator(".catalog-entry-grid").get_by_role(role, name=name, exact=True)
                expect(entry).to_be_visible()
                size = entry.bounding_box()
                assert size and size["width"] == 100 and 79 <= size["height"] <= 84, (route_path, size)
                if route_path == "skills":
                    page.get_by_role("searchbox", name="Search skills").fill("evidence")
                    expect(page.get_by_role("button", name="Structure analysis", exact=True)).to_have_count(0)
                    entry.click()
                    details = page.get_by_role("dialog", name=name, exact=True)
                    expect(details.get_by_text("rna-review", exact=True)).to_be_visible()
                    expect(details.get_by_text("3", exact=True)).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(entry).to_be_focused()
            assert not errors, errors
            results.append({"theme": theme, "width": width, "card": bounds, "panel": panel_bounds, "search_focus": focus, "tool_history": histories, "page_errors": errors})
            context.close()
        browser.close()
    (screenshots / "inspection.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5197")
    parser.add_argument("--executable", required=True)
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/pskit-catalog-tools"))
    args = parser.parse_args()
    check(args.base_url.rstrip("/"), args.executable, args.screenshots)
