import type { RunView } from "./events";
import type { TranslationKey } from "../../i18n/translations";

const errorKeys: Record<string, TranslationKey> = {
  PI_RUN_FAILED: "agent.piRunFailed",
  PI_RESUME_FAILED: "agent.piResumeFailed",
  PI_RESUME_UNSAFE_RETRY: "agent.piResumeUnsafeRetry",
  PI_RUN_INTERRUPTED: "agent.piInterrupted",
  AF3_TASK_FAILED: "agent.af3TaskFailed",
  AF3_COMPUTE_UNAVAILABLE: "agent.af3ComputeUnavailable",
  AF3_EXECUTION_TIMEOUT: "agent.af3ExecutionTimeout",
  MODEL_GATEWAY_QUOTA_EXHAUSTED: "agent.modelQuotaExhausted",
  MODEL_GATEWAY_AUTH_FAILED: "agent.modelAuthFailed",
  MODEL_GATEWAY_RATE_LIMITED: "agent.modelRateLimited",
  TOKEN_QUOTA_EXCEEDED: "agent.tokenQuotaResume",
};

export function localizedRunError(
  run: RunView, t: (key: TranslationKey) => string,
): string | undefined {
  if (run.status !== "failed") return run.error;
  const failure = run.events.find((event) => event.type === "run.failed");
  if (failure?.type !== "run.failed") return run.error;
  const key = errorKeys[failure.data.code];
  return key ? t(key) : run.error;
}
