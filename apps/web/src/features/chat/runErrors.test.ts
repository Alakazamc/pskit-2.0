import { expect, it } from "vitest";
import { emptyRun } from "./events";
import { localizedRunError } from "./runErrors";

it("localizes the AF3 execution deadline failure", () => {
  const view = {
    ...emptyRun,
    status: "failed" as const,
    error: "AlphaFold 3 任务超过最长执行时间",
    events: [{
      id: "event-1", run_id: "run-1", type: "run.failed" as const,
      data: { code: "AF3_EXECUTION_TIMEOUT", message: "AlphaFold 3 任务超过最长执行时间" },
    }],
  };
  expect(localizedRunError(view, (key) => key)).toBe("agent.af3ExecutionTimeout");
});

it("localizes an unsafe Pi resume without exposing a raw backend message", () => {
  const view = {
    ...emptyRun,
    status: "failed" as const,
    error: "工具执行结果不确定，已停止自动重试",
    events: [{
      id: "event-1", run_id: "run-1", type: "run.failed" as const,
      data: { code: "PI_RESUME_UNSAFE_RETRY", message: "工具执行结果不确定，已停止自动重试" },
    }],
  };
  expect(localizedRunError(view, (key) => key)).toBe("agent.piResumeUnsafeRetry");
});
