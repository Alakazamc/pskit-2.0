import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import { useComposerStore } from "./composerStore";
import { LanguageProvider } from "../../i18n/LanguageProvider";

describe("Composer file references", () => {
  beforeEach(() => useComposerStore.getState().clear());
  afterEach(() => { cleanup(); window.localStorage.removeItem("research_language"); });

  it("uploads a text file through the API callback and removes the chip", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn().mockResolvedValue({ id: "file-1", name: "notes.txt" });
    const onSend = vi.fn().mockResolvedValue(true);
    const { container } = render(<Composer onSend={onSend} onUpload={onUpload} skills={[]} resources={[]} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["notes"], "notes.txt", { type: "text/plain" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "移除 notes.txt" })).toBeInTheDocument());
    expect(screen.queryByText(/文本最多 1 MiB/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("消息内容"), "Analyze this");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ attachments: [{ id: "file-1", name: "notes.txt" }] }));
    await user.upload(input, new File(["notes"], "notes.txt", { type: "text/plain" }));
    await user.click(await screen.findByRole("button", { name: "移除 notes.txt" }));
    expect(screen.queryByRole("button", { name: "移除 notes.txt" })).not.toBeInTheDocument();
  });

  it("shows a failed upload as a dismissible alert above the composer", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn().mockRejectedValue(new Error("offline"));
    const { container } = render(<Composer onSend={vi.fn()} onUpload={onUpload} skills={[]} resources={[]} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(input, new File(["notes"], "notes.txt", { type: "text/plain" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("文件上传失败");
    expect(alert.closest(".composer-surface")).toBeNull();
    await user.click(within(alert).getByRole("button", { name: "关闭通知" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows upload progress and a compact ready preview", async () => {
    const user = userEvent.setup();
    let finishUpload!: (ref: { id: string; name: string }) => void;
    const onUpload = vi.fn((_file: File, onProgress: (value: number) => void) => {
      onProgress(63);
      return new Promise<{ id: string; name: string }>((resolve) => { finishUpload = resolve; });
    });
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload} skills={[]} resources={[]} /></LanguageProvider>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(input, new File(["%PDF-1.4"], "paper.pdf", { type: "application/pdf" }));
    expect(screen.getByRole("progressbar", { name: /paper.pdf/ })).toHaveAttribute("aria-valuenow", "63");
    expect(screen.getByRole("button", { name: "发送消息" })).toBeDisabled();

    finishUpload({ id: "file-1", name: "paper.pdf" });
    expect(await screen.findByRole("group", { name: "已上传 paper.pdf" })).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText(/文本最多 1 MiB/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "移除 paper.pdf" }));
    expect(screen.queryByRole("group", { name: "已上传 paper.pdf" })).not.toBeInTheDocument();
  });

  it("does not claim a percentage before the upload reports progress", async () => {
    const user = userEvent.setup();
    let finishUpload!: (ref: { id: string; name: string }) => void;
    const onUpload = vi.fn(() => new Promise<{ id: string; name: string }>((resolve) => { finishUpload = resolve; }));
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload} skills={[]} resources={[]} /></LanguageProvider>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(input, new File(["notes"], "notes.txt", { type: "text/plain" }));

    const progress = screen.getByRole("progressbar", { name: "正在上传 notes.txt" });
    expect(progress).not.toHaveAttribute("aria-valuenow");
    expect(progress).not.toHaveTextContent("0%");

    finishUpload({ id: "file-1", name: "notes.txt" });
    expect(await screen.findByRole("group", { name: "已上传 notes.txt" })).toBeInTheDocument();
  });

  it.each([
    ["zh", "每轮对话最多只能上传 10 个文件，请在下一轮上传其余文件。"],
    ["en", "Upload at most 10 files per turn. Upload the remaining files in your next turn."],
  ])("keeps the first ten files and only warns on overflow in %s", async (language, warning) => {
    window.localStorage.setItem("research_language", language);
    const user = userEvent.setup();
    const onUpload = vi.fn(async (file: File) => ({ id: file.name, name: file.name }));
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload} skills={[]} resources={[]} /></LanguageProvider>);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const files = Array.from({ length: 12 }, (_, index) => new File(["notes"], `notes-${index + 1}.txt`, { type: "text/plain" }));

    await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, files);

    expect(await screen.findByRole("alert")).toHaveTextContent(warning);
    await waitFor(() => expect(screen.getAllByRole("group")).toHaveLength(10));
    expect(screen.queryByText("notes-11.txt")).not.toBeInTheDocument();
    expect(screen.queryByText("notes-12.txt")).not.toBeInTheDocument();
    expect(onUpload.mock.calls.map(([file]) => file.name)).toEqual(files.slice(0, 10).map((file) => file.name));
  });

  it("counts separate uploads in the same turn and resets only after a successful send", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn(async (file: File) => ({ id: file.name, name: file.name }));
    const onSend = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const { container } = render(<LanguageProvider><Composer onSend={onSend} onUpload={onUpload} skills={[]} resources={[]} /></LanguageProvider>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = Array.from({ length: 11 }, (_, index) => new File(["notes"], `notes-${index + 1}.txt`, { type: "text/plain" }));

    await user.upload(input, files.slice(0, 4));
    await user.upload(input, files.slice(4, 10));
    expect(screen.getAllByRole("group")).toHaveLength(10);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.upload(input, files[10]);
    expect(screen.getByRole("alert")).toHaveTextContent("每轮对话最多只能上传 10 个文件");
    expect(onUpload).toHaveBeenCalledTimes(10);

    await user.type(screen.getByLabelText("消息内容"), "Read these");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(screen.getAllByRole("group")).toHaveLength(10);
    await user.upload(input, files[10]);
    expect(onUpload).toHaveBeenCalledTimes(10);
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend.mock.calls[1][0].attachments).toHaveLength(10);
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await user.upload(input, files[10]);
    expect(await screen.findByRole("group", { name: "已上传 notes-11.txt" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reserves places for simultaneous drops while earlier files are still uploading", async () => {
    const finishes: (() => void)[] = [];
    let firstBatchStarted!: () => void;
    const started = new Promise<void>((resolve) => { firstBatchStarted = resolve; });
    const onUpload = vi.fn((file: File) => new Promise<{ id: string; name: string }>((resolve) => {
      finishes.push(() => resolve({ id: file.name, name: file.name }));
      if (finishes.length === 9) firstBatchStarted();
    }));
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload} skills={[]} resources={[]} /></LanguageProvider>);
    const dropTarget = container.querySelector('[role="presentation"]') as HTMLElement;
    const files = Array.from({ length: 11 }, (_, index) => new File(["notes"], `notes-${index + 1}.txt`, { type: "text/plain" }));
    const dataTransfer = (files: File[]) => ({ files, types: ["Files"], items: files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })) });

    await act(async () => {
      fireEvent.drop(dropTarget, { dataTransfer: dataTransfer(files.slice(0, 9)) });
      await started;
      fireEvent.drop(dropTarget, { dataTransfer: dataTransfer(files.slice(9)) });
    });

    expect(screen.getAllByRole("progressbar")).toHaveLength(10);
    expect(screen.getByRole("alert")).toHaveTextContent("每轮对话最多只能上传 10 个文件");
    expect(onUpload).toHaveBeenCalledTimes(10);
    await act(async () => finishes.forEach((finish) => finish()));
    expect(screen.getAllByRole("group")).toHaveLength(10);
  });

  it("releases a canceled upload's place and ignores its late completion", async () => {
    const user = userEvent.setup();
    const finishes = new Map<string, () => void>();
    const onUpload = vi.fn((file: File) => new Promise<{ id: string; name: string }>((resolve) => {
      finishes.set(file.name, () => resolve({ id: file.name, name: file.name }));
    }));
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload} skills={[]} resources={[]} /></LanguageProvider>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = Array.from({ length: 11 }, (_, index) => new File(["notes"], `notes-${index + 1}.txt`, { type: "text/plain" }));
    await user.upload(input, files.slice(0, 10));
    await user.click(screen.getByRole("button", { name: "移除 notes-10.txt" }));
    await user.upload(input, files[10]);

    expect(screen.getAllByRole("progressbar")).toHaveLength(10);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => finishes.forEach((finish) => finish()));
    expect(screen.getAllByRole("group")).toHaveLength(10);
    expect(screen.queryByRole("group", { name: "已上传 notes-10.txt" })).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "已上传 notes-11.txt" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "移除 notes-1.txt" }));
    await user.upload(input, files[0]);
    await act(async () => finishes.get("notes-1.txt")!());
    expect(screen.getAllByRole("group")).toHaveLength(10);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an empty state when the API has no skills or resources", async () => {
    const user = userEvent.setup();
    render(<Composer onSend={vi.fn()} onUpload={vi.fn()} skills={[]} resources={[]} />);
    await user.type(screen.getByLabelText("消息内容"), "/");
    expect(screen.getByText("暂无可用技能")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("消息内容"));
    await user.type(screen.getByLabelText("消息内容"), "@");
    expect(screen.getByText("暂无可用资源")).toBeInTheDocument();
  });

  it("uses English labels and empty states when English is selected", async () => {
    window.localStorage.setItem("research_language", "en");
    const user = userEvent.setup();
    render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={vi.fn()} skills={[]} resources={[]} /></LanguageProvider>);
    await user.type(screen.getByLabelText("Message"), "/");
    expect(screen.getByText("No skills available")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send message" })).toBeInTheDocument();
  });

  it("uses English group labels for project and global Skills", async () => {
    window.localStorage.setItem("research_language", "en");
    const user = userEvent.setup();
    render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={vi.fn()} resources={[]}
      skills={[{ id: "local", name: "Local", description: "" }, { id: "global", name: "Global", description: "" }]}
      projectSkillIds={["local"]} /></LanguageProvider>);
    await user.type(screen.getByLabelText("Message"), "/");
    expect(screen.getByText("Project Skills")).toBeInTheDocument();
    expect(screen.getByText("Global Skills")).toBeInTheDocument();
  });

  it("searches gateway models and sends the selected model and thinking level", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(true);
    render(<LanguageProvider><Composer onSend={onSend} onUpload={vi.fn()} skills={[]} resources={[]}
      models={[{ id: "text-model", supports_images: false, reasoning_levels: [] },
        { id: "anthropic/claude-opus-4-8", supports_images: true, reasoning_levels: ["medium", "high", "xhigh"] }]} /></LanguageProvider>);
    await user.click(screen.getByRole("button", { name: /选择模型/ }));
    await user.click(screen.getByRole("button", { name: /切换模型/ }));
    await user.type(screen.getByRole("searchbox", { name: "搜索模型" }), "opus");
    await user.click(screen.getByRole("button", { name: /claude-opus-4-8/ }));
    const slider = screen.getByRole("slider", { name: "推理强度" });
    expect(slider).toHaveAttribute("aria-orientation", "vertical");
    fireEvent.change(slider, { target: { value: "2" } });
    expect(slider).toHaveAttribute("aria-valuetext", "高");
    await user.type(screen.getByLabelText("消息内容"), "解释一下");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({
      model: "anthropic/claude-opus-4-8", reasoning_effort: "high",
    }));
    expect(screen.getByRole("button", { name: /选择模型/ })).toHaveTextContent("高");
  });

  it("changes models inside the settings panel and returns to the thinking control", async () => {
    const user = userEvent.setup();
    render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={vi.fn()} skills={[]} resources={[]}
      models={[{ id: "first-model", supports_images: false, reasoning_levels: ["medium", "high"] },
        { id: "second-model", supports_images: true, reasoning_levels: ["medium", "high", "xhigh"] }]} /></LanguageProvider>);

    await user.click(screen.getByRole("button", { name: /选择模型与推理强度/ }));
    const panel = screen.getByRole("dialog", { name: "模型与推理设置" });
    expect(within(panel).getByRole("slider", { name: "推理强度" })).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: /切换模型/ }));
    await user.type(screen.getByRole("searchbox", { name: "搜索模型" }), "second");
    await user.click(screen.getByRole("button", { name: "second-model" }));

    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    const slider = within(panel).getByRole("slider", { name: "推理强度" });
    expect(slider).toHaveAttribute("aria-valuetext", "默认");
    fireEvent.change(slider, { target: { value: "3" } });
    expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("second-model极高");
  });

  it("shows an empty model catalog and asks to reselect a removed model", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(true);
    useComposerStore.getState().setModel("removed-model");
    const { rerender } = render(<LanguageProvider><Composer onSend={onSend} onUpload={vi.fn()} skills={[]} resources={[]} models={[]} /></LanguageProvider>);
    await user.click(screen.getByRole("button", { name: /选择模型/ }));
    expect(screen.getByText("暂无可用模型")).toBeInTheDocument();
    rerender(<LanguageProvider><Composer onSend={onSend} onUpload={vi.fn()} skills={[]} resources={[]}
      models={[{ id: "available-model", supports_images: false, reasoning_levels: [] }]} /></LanguageProvider>);
    expect(screen.getByText("之前选择的模型已不可用，请重新选择")).toBeInTheDocument();
    await user.type(screen.getByLabelText("消息内容"), "hello");
    expect(screen.getByRole("button", { name: "发送消息" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /选择模型/ }));
    await user.click(screen.getByRole("button", { name: /切换模型/ }));
    await user.click(screen.getByRole("button", { name: "available-model" }));
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ model: "available-model" }));
  });

  it("accepts image files only when the selected model supports images", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn().mockResolvedValue({ id: "image-1", name: "image.png" });
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload}
      skills={[]} resources={[]} models={[{ id: "text-model", supports_images: false },
        { id: "vision-model", supports_images: true }]} /></LanguageProvider>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const image = new File(["\x89PNG\r\n\x1a\n"], "image.png", { type: "image/png" });
    await user.upload(input, image);
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("支持图片的模型");
    await user.click(screen.getByRole("button", { name: /选择模型/ }));
    await user.click(screen.getByRole("button", { name: /切换模型/ }));
    await user.click(screen.getByRole("button", { name: /vision-model/ }));
    await user.upload(input, image);
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
  });

  it("counts images toward the same ten-file limit", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn(async (file: File) => ({ id: file.name, name: file.name }));
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload}
      skills={[]} resources={[]} models={[{ id: "vision-model", supports_images: true }]} /></LanguageProvider>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, Array.from({ length: 10 }, (_, index) => new File(["image"], `image-${index + 1}.png`, { type: "image/png" })));
    await waitFor(() => expect(screen.getAllByRole("group")).toHaveLength(10));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.upload(input, new File(["image"], "image-11.png", { type: "image/png" }));
    expect(onUpload).toHaveBeenCalledTimes(10);
    expect(screen.getByRole("alert")).toHaveTextContent("每轮对话最多只能上传 10 个文件");
  });
});
