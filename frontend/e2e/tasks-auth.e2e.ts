import { expect, test, type Route } from "@playwright/test";

const user = { id: "user", username: "test-user", role: "user" };
const timestamp = "2026-01-01T00:00:00Z";
const task = (index: number) => ({
  id: `task-${index}`, session_id: "A", tool_call_id: null, task_type: `analysis-${index}`,
  status: "succeeded", progress: 1, error_type: null, error_message: null,
  input: null, output: null, artifacts: [], retry_of_task_id: null, retry_task_id: null,
  created_at: timestamp, updated_at: timestamp, started_at: timestamp, finished_at: timestamp,
});

test("task pagination reaches old tasks and ignores stale polling responses", async ({ page }) => {
  await page.clock.install();
  const offsets: number[] = [];
  let release!: () => void;
  const oldPoll = new Promise<void>((resolve) => { release = resolve; });
  let firstPageRequests = 0;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: user });
    if (url.pathname === "/api/tasks") {
      const offset = Number(url.searchParams.get("offset"));
      const limit = Number(url.searchParams.get("limit"));
      offsets.push(offset);
      if (offset === 0 && ++firstPageRequests === 2) await oldPoll;
      return route.fulfill({ json: Array.from({ length: Math.min(limit, 121 - offset) }, (_, index) => task(offset + index)) });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto("/tasks");
  await expect(page.locator(".task-row")).toHaveCount(20);
  await page.clock.fastForward(3000); // Hold the page-one background poll.
  await expect.poll(() => firstPageRequests).toBe(2);
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByText("analysis-20", { exact: true })).toBeVisible();
  release();
  await page.clock.fastForward(3000);
  await expect.poll(() => offsets.filter((offset) => offset === 20).length).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("analysis-20", { exact: true })).toBeVisible();
  await expect(page.getByText("analysis-0", { exact: true })).toHaveCount(0);
  for (let next = 0; next < 5; next += 1) {
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.getByText(`analysis-${40 + next * 20}`, { exact: true })).toBeVisible();
  }
  await expect(page.locator(".task-row")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "下一页", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(page.getByText("analysis-100", { exact: true })).toBeVisible();
});

test("an expired session returns to login and resumes the protected destination", async ({ page }) => {
  let expired = true;
  await page.route("**/api/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: user });
    if (path === "/api/tasks") return expired
      ? route.fulfill({ status: 401, json: { detail: "Session expired" } })
      : route.fulfill({ json: [] });
    if (path === "/api/auth/login") {
      expired = false;
      return route.fulfill({ json: user });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto("/tasks");
  await expect(page).toHaveURL(/\/login\?redirect=/);
  expect(new URL(page.url()).searchParams.get("redirect")).toBe("/tasks");
  await expect(page.locator(".user-chip")).toHaveCount(0);
  await page.getByLabel("用户名", { exact: true }).fill("test-user");
  await page.getByLabel("密码", { exact: true }).fill("valid-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/tasks$/);
  await expect(page.getByText("暂无任务", { exact: true })).toBeVisible();
  await expect(page.locator(".user-chip")).toContainText("test-user");
});

test("guest and incorrect-password 401 responses stay on login without a redirect loop", async ({ page }) => {
  let meRequests = 0;
  await page.route("**/api/**", async (route) => {
    if (new URL(route.request().url()).pathname === "/api/auth/me") meRequests += 1;
    return route.fulfill({ status: 401, json: { detail: "用户名或密码不正确" } });
  });
  await page.goto("/login?redirect=/tasks");
  await page.getByLabel("用户名", { exact: true }).fill("test-user");
  await page.getByLabel("密码", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".auth-form .error-line")).toHaveText("用户名或密码不正确");
  await expect(page).toHaveURL(/\/login\?redirect=\/tasks$/);
  expect(meRequests).toBe(1);
});
