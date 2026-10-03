import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getByText(/文本最多 1 MiB/)).toBeInTheDocument();
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
    expect(screen.getByText(/文本最多 1 MiB/)).toBeInTheDocument();
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

  it("offers gateway models and sends the selected model with the message", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(true);
    render(<LanguageProvider><Composer onSend={onSend} onUpload={vi.fn()} skills={[]} resources={[]}
      models={[{ id: "text-model", supports_images: false },
        { id: "vision-model", supports_images: true }]} /></LanguageProvider>);
    await user.selectOptions(screen.getByRole("combobox", { name: "模型" }), "vision-model");
    await user.type(screen.getByLabelText("消息内容"), "解释一下");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ model: "vision-model" }));
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
    await user.selectOptions(screen.getByRole("combobox", { name: "模型" }), "vision-model");
    await user.upload(input, image);
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
  });

  it("limits a message to two images before uploading", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn(async (file: File) => ({ id: file.name, name: file.name }));
    const { container } = render(<LanguageProvider><Composer onSend={vi.fn()} onUpload={onUpload}
      skills={[]} resources={[]} models={[{ id: "vision-model", supports_images: true }]} /></LanguageProvider>);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, [
      new File(["image"], "one.png", { type: "image/png" }),
      new File(["image"], "two.png", { type: "image/png" }),
    ]);
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(2));
    await user.upload(input, new File(["image"], "three.png", { type: "image/png" }));
    expect(onUpload).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).toHaveTextContent("最多附加 2 张图片");
  });
});
