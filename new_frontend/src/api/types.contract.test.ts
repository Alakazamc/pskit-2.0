import { expect, expectTypeOf, it } from "vitest";
import type * as Api from "./generated/types.gen";
import type {
  McpResult, McpTool, Message, MessagePart, MessageRequest,
  ProjectSkillSettings, RecoveryResponse, SignupResponse, ToolRun,
} from "./types";

it("keeps frontend API models assignable to generated Python contracts", () => {
  const part: Api.Message["parts"][number] = null as unknown as MessagePart;
  const message: Api.Message = null as unknown as Message;
  expect(part).toBeNull();
  expect(message).toBeNull();
  expectTypeOf<MessageRequest>().toExtend<Api.MessageRequest>();
  expectTypeOf<McpTool>().toExtend<Api.McpTool>();
  expectTypeOf<McpResult>().toExtend<Api.McpInvokeResult>();
  expectTypeOf<ToolRun>().toExtend<Api.ToolRun>();
  expectTypeOf<ProjectSkillSettings>().toExtend<Api.ProjectSkillSettings>();
  expectTypeOf<SignupResponse>().toExtend<Api.SignupResponse>();
  expectTypeOf<RecoveryResponse>().toExtend<Api.RecoveryResponse>();
});

it("matches the Python approval response shape", () => {
  expectTypeOf<Awaited<ReturnType<import("./types").ResearchApi["decideApproval"]>>>()
    .toEqualTypeOf<Api.ApprovalDecisionResponse>();
});
