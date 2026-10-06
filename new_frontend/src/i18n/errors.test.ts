import { expect, it } from "vitest";
import { ApiError } from "../api/http";
import { errorTranslationKey } from "./errors";

it("explains the per-turn attachment limit from the server", () => {
  expect(errorTranslationKey(new ApiError(422, { code: "TOO_MANY_ATTACHMENTS" })))
    .toBe("error.tooManyAttachments");
});

it("maps remote MCP failures to a user-facing message key", () => {
  expect(errorTranslationKey(new ApiError(503, { code: "MCP_UPSTREAM_UNAVAILABLE" })))
    .toBe("error.mcpUnavailable");
  expect(errorTranslationKey(new ApiError(502, { code: "MCP_UPSTREAM_FAILED" })))
    .toBe("error.mcpUnavailable");
  expect(errorTranslationKey(new ApiError(429, { code: "MCP_CAPACITY_EXCEEDED" })))
    .toBe("error.mcpBusy");
  expect(errorTranslationKey(new ApiError(409, { code: "MCP_TOOL_CALL_OUTCOME_UNKNOWN" })))
    .toBe("error.mcpOutcomeUnknown");
  expect(errorTranslationKey(new ApiError(409, { code: "MCP_TOOL_CALL_CONFLICT" })))
    .toBe("error.mcpCallConflict");
});

it("maps PDF parsing deadlines to a bilingual message key", () => {
  expect(errorTranslationKey(new ApiError(504, { code: "FILE_PROCESSING_TIMEOUT" })))
    .toBe("error.fileProcessingTimeout");
  expect(errorTranslationKey(new ApiError(500, { code: "FILE_PROCESSING_FAILED" })))
    .toBe("error.fileProcessingFailed");
  expect(errorTranslationKey(new ApiError(429, { code: "FILE_PROCESSING_BUSY" })))
    .toBe("error.fileProcessingBusy");
});

it("maps a reused AF3 submission key with changed arguments", () => {
  expect(errorTranslationKey(new ApiError(409, { code: "AF3_IDEMPOTENCY_CONFLICT" })))
    .toBe("error.af3IdempotencyConflict");
});

it("explains guest-only limits and account upgrade errors", () => {
  expect(errorTranslationKey(new ApiError(403, { code: "LOGIN_REQUIRED" })))
    .toBe("guest.gpuRequiresAccount");
  expect(errorTranslationKey(new ApiError(413, { code: "STORAGE_QUOTA_EXCEEDED" })))
    .toBe("guest.storageQuota");
  expect(errorTranslationKey(new ApiError(409, { code: "EMAIL_ALREADY_IN_USE" })))
    .toBe("guest.emailInUse");
  expect(errorTranslationKey(new ApiError(401, { code: "INVALID_OTP" })))
    .toBe("error.invalidOtp");
  expect(errorTranslationKey(new ApiError(429, { code: "ANONYMOUS_RATE_LIMITED" })))
    .toBe("guest.rateLimited");
  expect(errorTranslationKey(new ApiError(410, { code: "GUEST_ACCOUNT_DELETING" })))
    .toBe("guest.accountDeleting");
});

it("maps protected auth failures to stable bilingual keys", () => {
  expect(errorTranslationKey(new ApiError(422, { code: "CAPTCHA_REQUIRED" })))
    .toBe("error.captchaRequired");
  expect(errorTranslationKey(new ApiError(422, { code: "CAPTCHA_INVALID" })))
    .toBe("error.captchaInvalid");
  expect(errorTranslationKey(new ApiError(503, { code: "AUTH_CAPTCHA_UNAVAILABLE" })))
    .toBe("error.captchaUnavailable");
  expect(errorTranslationKey(new ApiError(503, { code: "IDENTITY_UNAVAILABLE" })))
    .toBe("error.identityUnavailable");
  expect(errorTranslationKey(new ApiError(429, { code: "AUTH_RATE_LIMITED" }, "30")))
    .toBe("error.authRateLimited");
});
