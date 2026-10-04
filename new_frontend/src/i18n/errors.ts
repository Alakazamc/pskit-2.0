import { ApiError } from "../api/http";
import type { TranslationKey } from "./translations";

const codeKeys: Record<string, TranslationKey> = {
  TOKEN_QUOTA_EXCEEDED: "error.tokenQuota",
  GPU_DAILY_QUOTA_EXCEEDED: "error.gpuQuota",
  MODEL_NOT_CONFIGURED: "error.modelMissing",
  MODEL_UNAVAILABLE: "error.modelUnavailable",
  MODEL_REASONING_UNAVAILABLE: "error.modelReasoningUnavailable",
  MODEL_DOES_NOT_SUPPORT_IMAGES: "error.modelNoImages",
  TOO_MANY_ATTACHMENTS: "error.tooManyAttachments",
  PI_NOT_CONFIGURED: "error.agentMissing",
  MCP_NOT_CONFIGURED: "error.mcpMissing",
  MCP_UPSTREAM_UNAVAILABLE: "error.mcpUnavailable",
  MCP_UPSTREAM_FAILED: "error.mcpUnavailable",
  MCP_LEDGER_UNAVAILABLE: "error.mcpUnavailable",
  MCP_CAPACITY_EXCEEDED: "error.mcpBusy",
  MCP_TOOL_CALL_OUTCOME_UNKNOWN: "error.mcpOutcomeUnknown",
  MCP_TOOL_CALL_CONFLICT: "error.mcpCallConflict",
  AF3_NOT_CONFIGURED: "error.af3Missing",
  AF3_IDEMPOTENCY_CONFLICT: "error.af3IdempotencyConflict",
  LOGIN_REQUIRED: "guest.gpuRequiresAccount",
  STORAGE_QUOTA_EXCEEDED: "guest.storageQuota",
  EMAIL_ALREADY_IN_USE: "guest.emailInUse",
  INVALID_OTP: "error.invalidOtp",
  ANONYMOUS_RATE_LIMITED: "guest.rateLimited",
  CAPTCHA_REQUIRED: "guest.captchaRequired",
  GUEST_ACCOUNT_DELETING: "guest.accountDeleting",
  GOOGLE_IDENTITY_CONFLICT: "guest.googleConflict",
  GOOGLE_LINK_REJECTED: "guest.googleFailed",
  UPGRADE_SESSION_MISMATCH: "guest.upgradeFailed",
  UPGRADE_IDENTITY_MISMATCH: "guest.upgradeFailed",
  UPGRADE_NOT_CONFIRMED: "guest.upgradeFailed",
  UNSUPPORTED_FILE_TYPE: "error.unsupportedFile",
  FILE_TOO_LARGE: "error.fileTooLarge",
  FILE_SIZE_MISMATCH: "error.fileSizeMismatch",
  FILE_PROCESSING_TIMEOUT: "error.fileProcessingTimeout",
  FILE_PROCESSING_FAILED: "error.fileProcessingFailed",
  FILE_PROCESSING_BUSY: "error.fileProcessingBusy",
  PDF_NO_EXTRACTABLE_TEXT: "error.pdfNoText",
  INVALID_PDF: "error.invalidPdf",
  INVALID_IMAGE: "error.invalidImage",
  PDF_TOO_MANY_PAGES: "error.pdfTooManyPages",
  INVALID_TEXT_ENCODING: "error.invalidTextEncoding",
  CONTEXT_NOT_FOUND: "error.contextMissing",
};

export function errorTranslationKey(error: unknown): TranslationKey | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code && codeKeys[error.code]) return codeKeys[error.code];
  return error.status === 401 ? "error.authRequired" : null;
}
