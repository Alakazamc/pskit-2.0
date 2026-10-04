import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import { useComposerStore } from "./composerStore";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import type { ModelOption } from "../../api/types";

afterEach(() => {
  cleanup();
  useComposerStore.getState().clear();
  window.localStorage.clear();
  vi.restoreAllMocks();
  onSend.mockReset().mockResolvedValue(false);
  onUpload.mockReset();
});

it("keeps an upload completing from an old mount in its original draft without overwriting newer text", async () => {
  const actor = userEvent.setup();
  let finish!: (file: { id: string; name: string }) => void;
  onUpload.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const rendered = render(view("alice", "session-a"));
  await actor.type(screen.getByLabelText("消息内容"), "旧文字");
  await actor.upload(rendered.container.querySelector('input[type="file"]') as HTMLInputElement,
    new File(["notes"], "notes.txt", { type: "text/plain" }));
  rendered.rerender(view("alice", "session-b"));
  await actor.type(screen.getByLabelText("消息内容"), "B 的文字");
  rendered.rerender(view("alice", "session-a"));
  await actor.clear(screen.getByLabelText("消息内容"));
  await actor.type(screen.getByLabelText("消息内容"), "A 的新文字");
  await act(async () => { finish({ id: "file-a", name: "notes.txt" }); });
  expect(await screen.findByRole("button", { name: "移除 notes.txt" })).toBeInTheDocument();
  expect(screen.getByLabelText("消息内容")).toHaveValue("A 的新文字");
  rendered.rerender(view("alice", "session-b"));
  await waitFor(() => expect(screen.getByLabelText("消息内容")).toHaveValue("B 的文字"));
  expect(screen.queryByRole("button", { name: "移除 notes.txt" })).not.toBeInTheDocument();
});

const onSend = vi.fn().mockResolvedValue(false);
const onUpload = vi.fn();
const models: ModelOption[] = [
  { id: "first-model", supports_images: false, reasoning_levels: ["medium", "high"] },
  { id: "second-model", supports_images: true, reasoning_levels: ["medium", "high"] },
];
const view = (userId: string, conversationId: string, options = models) => <LanguageProvider><Composer
  draftScope={{ userId, conversationId }}
  onSend={onSend} onUpload={onUpload} skills={[]} resources={[]} models={options}
/></LanguageProvider>;

it("restores each user's conversation draft without leaking it to the next conversation", async () => {
  const actor = userEvent.setup();
  const rendered = render(view("alice", "session-a"));
  await actor.type(screen.getByLabelText("消息内容"), "A 的未发送消息");

  rendered.rerender(view("alice", "session-b"));
  expect(screen.getByLabelText("消息内容")).toHaveValue("");
  await actor.type(screen.getByLabelText("消息内容"), "B 的未发送消息");
  rendered.rerender(view("alice", "session-a"));
  expect(screen.getByLabelText("消息内容")).toHaveValue("A 的未发送消息");

  rendered.rerender(view("bob", "session-a"));
  expect(screen.getByLabelText("消息内容")).toHaveValue("");
  cleanup();
  render(view("alice", "session-b"));
  expect(screen.getByLabelText("消息内容")).toHaveValue("B 的未发送消息");
});

it("does not carry pending upload controls into another conversation when the composer is reused", async () => {
  onUpload.mockImplementationOnce(() => new Promise(() => undefined));
  const actor = userEvent.setup();
  const rendered = render(view("alice", "session-a"));
  await actor.upload(rendered.container.querySelector('input[type="file"]') as HTMLInputElement,
    new File(["notes"], "notes.txt", { type: "text/plain" }));
  expect(screen.getByRole("progressbar")).toBeInTheDocument();
  rendered.rerender(view("alice", "session-b"));
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "移除 notes.txt" })).not.toBeInTheDocument();
});

it("pins the displayed default model locally and restores model and effort across remounts", async () => {
  const actor = userEvent.setup();
  render(view("alice", "session-a"));
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("first-model");
  cleanup();
  render(view("alice", "session-a", [...models].reverse()));
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("first-model");

  await actor.click(screen.getByRole("button", { name: /选择模型与推理强度/ }));
  fireEvent.change(screen.getByRole("slider", { name: "推理强度" }), { target: { value: "2" } });
  await actor.keyboard("{Escape}");
  cleanup();
  const rendered = render(view("alice", "session-a"));
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("first-model高");
  rendered.rerender(view("alice", "session-b", [...models].reverse()));
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("second-model默认");
  rendered.rerender(view("alice", "session-a"));
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("first-model高");

  await actor.click(screen.getByRole("button", { name: /选择模型与推理强度/ }));
  await actor.click(screen.getByRole("button", { name: /切换模型/ }));
  await actor.click(screen.getByRole("button", { name: "second-model" }));
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("second-model默认");
});

it("clears only the submitted text and leaves another conversation and newer typing intact", async () => {
  const actor = userEvent.setup();
  let finish!: (accepted: boolean) => void;
  onSend.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const rendered = render(view("alice", "session-a"));
  await actor.type(screen.getByLabelText("消息内容"), "已提交内容");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  rendered.rerender(view("alice", "session-b"));
  await actor.type(screen.getByLabelText("消息内容"), "B 的草稿");
  rendered.rerender(view("alice", "session-a"));
  await actor.clear(screen.getByLabelText("消息内容"));
  await actor.type(screen.getByLabelText("消息内容"), "等待时输入的新内容");
  await act(async () => { finish(true); });
  expect(screen.getByLabelText("消息内容")).toHaveValue("等待时输入的新内容");
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("first-model默认");
  rendered.rerender(view("alice", "session-b"));
  expect(screen.getByLabelText("消息内容")).toHaveValue("B 的草稿");
});

it.each(["{broken-json", '{"version":0,"content":"old schema"}', '{"version":1,"content":null,"reasoning_effort":"invented","attachments":[null]}'])("recovers from malformed or unsupported local cache: %s", async (saved) => {
    window.localStorage.setItem("pskit.composer.v1:alice:session-a", saved);
    const actor = userEvent.setup();
    render(view("alice", "session-a"));
    expect(screen.getByLabelText("消息内容")).toHaveValue("");
    await actor.type(screen.getByLabelText("消息内容"), "可继续输入");
    expect(screen.getByLabelText("消息内容")).toHaveValue("可继续输入");
  });

it("keeps the current composer usable when browser storage is disabled", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("disabled"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("disabled"); });
  render(view("alice", "session-a"));
  const actor = userEvent.setup();
  await actor.type(screen.getByLabelText("消息内容"), "内存中的草稿");
  expect(screen.getByLabelText("消息内容")).toHaveValue("内存中的草稿");
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("first-model");
});

it("keeps current model preferences when storage can be read but writes exceed its quota", async () => {
  window.localStorage.setItem("pskit.composer.v1:alice:session-a", JSON.stringify({
    version: 1, content: "", model: "first-model", attachments: [], skills: [], resources: [],
  }));
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
  render(view("alice", "session-a"));
  const actor = userEvent.setup();
  await actor.click(screen.getByRole("button", { name: /选择模型与推理强度/ }));
  await actor.click(screen.getByRole("button", { name: /切换模型/ }));
  await actor.click(screen.getByRole("button", { name: "second-model" }));
  fireEvent.change(screen.getByRole("slider", { name: "推理强度" }), { target: { value: "2" } });
  await actor.keyboard("{Escape}");
  await actor.type(screen.getByLabelText("消息内容"), "仍可输入");
  expect(screen.getByLabelText("消息内容")).toHaveValue("仍可输入");
  expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("second-model高");
});
