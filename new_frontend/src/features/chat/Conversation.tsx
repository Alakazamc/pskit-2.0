import { useId, useLayoutEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronUp, Circle, FlaskConical, LoaderCircle } from "lucide-react";
import type { Message, MessagePart } from "../../api/types";
import type { RunView } from "./events";
import { MessageParts } from "./MessageParts";
import { ReplyCopyButton } from "./ReplyCopyButton";
import { useLanguage } from "../../i18n/LanguageProvider";
import { localizedRunError } from "./runErrors";
import { RunControls } from "../mono/RunControls";
import { useChatAutoscroll } from "./useChatAutoscroll";
import { MessageImageAttachment } from "./MessageImageAttachment";
import type { MessageResourceActions } from "./messagePartRegistry";

type AttachmentImages = { userId: string; loadImage: (id: string) => Promise<Blob> };

function AssistantWaitingIndicator() {
  const { t } = useLanguage();
  return <div className="assistant-response-waiting" role="status" aria-label={t("conversation.generating")}>
    <LoaderCircle className="message-spinner" size={20} aria-hidden="true" />
  </div>;
}

function UserMessage({ message, attachmentImages, resourceActions }: { message: Message; attachmentImages?: AttachmentImages; resourceActions?: MessageResourceActions }) {
  const { t } = useLanguage();
  const [expanded, setExpanded] = useState(false);
  const [canCollapse, setCanCollapse] = useState(false);
  const rowRef = useRef<HTMLElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const pendingAnchor = useRef<{ scroll: HTMLElement; element: HTMLElement; top: number } | null>(null);
  const textId = useId();
  const textParts = message.parts.filter((part) => part.type === "text");
  const imageParts = message.parts.filter((part) => part.type === "file" && /\.(png|jpe?g|gif|webp)$/i.test(part.name));
  const otherParts = message.parts.filter((part) => part.type !== "text" && !imageParts.includes(part));

  useLayoutEffect(() => {
    if (expanded || !textParts.length) return;
    const measure = () => {
      const text = textRef.current;
      if (text) setCanCollapse(text.scrollHeight > text.clientHeight + 1);
    };
    measure();
    if (typeof ResizeObserver === "undefined" || !rowRef.current) return;
    const observer = new ResizeObserver(measure);
    observer.observe(rowRef.current);
    return () => observer.disconnect();
  }, [expanded, message.parts, textParts.length]);

  useLayoutEffect(() => {
    const anchor = pendingAnchor.current;
    if (!anchor) return;
    pendingAnchor.current = null;
    anchor.scroll.scrollTop += anchor.element.getBoundingClientRect().top - anchor.top;
  }, [expanded]);

  const toggle = () => {
    const element = expanded ? toggleRef.current : rowRef.current;
    const scroll = rowRef.current?.closest<HTMLElement>(".conversation-scroll");
    if (element && scroll) pendingAnchor.current = { scroll, element, top: element.getBoundingClientRect().top };
    setExpanded((current) => !current);
  };

  return <article className="message-row user" ref={rowRef}>
    <div className="user-message-content">
      {imageParts.length > 0 && <div className="user-message-images" role="region" aria-label={t("composer.attachments")} tabIndex={0}>
        {imageParts.map((part) => part.type === "file" && (attachmentImages
          ? <MessageImageAttachment key={part.id} name={part.name} fileId={part.id} {...attachmentImages} />
          : <div key={part.id} className="user-message-image-fallback">{t("conversation.imageUnavailable")} · {part.name}</div>))}
      </div>}
      {(textParts.length > 0 || otherParts.length > 0) && <div className="message-body">
      {textParts.length > 0 && <div id={textId} ref={textRef} className={`user-message-text${expanded ? "" : " is-collapsed"}`}><MessageParts parts={textParts} /></div>}
      <MessageParts parts={otherParts} resourceActions={resourceActions} />
      {canCollapse && <button ref={toggleRef} className="user-message-toggle" type="button" aria-expanded={expanded} aria-controls={textId} onClick={toggle}>
        {expanded ? t("conversation.collapseMessage") : t("conversation.expandMessage")}
        {expanded ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      </button>}
      </div>}
    </div>
  </article>;
}

export function Conversation({ messages, run, runId, awaitingEvents = false, onCancel, onApproval, attachmentImages, resourceActions }: { messages: Message[]; run: RunView; runId?: string | null; awaitingEvents?: boolean; onCancel?: () => Promise<void>; onApproval?: (approvalId: string, decision: "approved" | "rejected") => Promise<void>; attachmentImages?: AttachmentImages; resourceActions?: MessageResourceActions }) {
  const { t } = useLanguage();
  const scrollRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  useChatAutoscroll(scrollRef, listRef, `${messages.length}:${run.text.length}:${run.status}:${run.tools.length}`, runId,
    messages.length > 0 || run.status !== "idle" || awaitingEvents);
  const runError = localizedRunError(run, t);
  const runActive = (run.status === "running" || run.status === "waiting" || awaitingEvents) && !run.approval;
  const runFinished = ["completed", "failed", "cancelled"].includes(run.status);
  const lastUserIndex = messages.reduce((last, message, index) => message.role === "user" ? index : last, -1);
  const runStartedAt = run.events.find((event) => event.created_at)?.created_at;
  const savedReplyAvailable = messages.some((message, index) => message.role === "assistant"
    && index > lastUserIndex
    && (!runStartedAt || Date.parse(message.created_at) >= Date.parse(runStartedAt)));
  const showRunReply = (run.status !== "idle" || awaitingEvents) && (run.status !== "completed"
    || (run.text.length > 0 && !savedReplyAvailable));
  const liveParts: MessagePart[] = [
    ...(runError ? [{ type: "error" as const, code: "RUN_FAILED", message: runError }]
      : run.text ? [{ type: "text" as const, text: run.text }] : []),
    ...run.tools.map((tool) => ({ type: "tool_call" as const, tool_call_id: tool.id, tool: tool.name, status: tool.status, summary: tool.summary })),
    ...(run.jobId && !["completed", "failed", "cancelled"].includes(run.status)
      ? [{ type: "progress" as const, label: run.jobLabel || t("conversation.backgroundTask"), value: Number.isFinite(run.progress) ? Math.round(run.progress) : 0 }]
      : []),
    ...run.artifacts.map((artifact) => ({ type: "artifact" as const, ...artifact })),
  ];
  return <div className="conversation-scroll" ref={scrollRef}>
    {messages.length === 0 && run.status === "idle" && !awaitingEvents ? <div className="empty-chat">
      <div className="empty-icon"><FlaskConical size={28} /></div>
      <span className="eyebrow">{t("workspace.eyebrow")}</span>
      <h2>{t("conversation.emptyTitle")}</h2>
      <p>{t("conversation.emptyDescription")}</p>
    </div> : <div className="message-list" ref={listRef}>
      {run.plan.length > 0 && <section className="conversation-plan" aria-label={t("agent.plan")}>
        <h3>{t("agent.plan")}</h3>
        <ol>{run.plan.map((step) => <li key={step.id}>
          <span className={`conversation-plan-status ${step.status}`}>
            {step.status === "completed" ? <Check size={14} /> : step.status === "in_progress" ? <LoaderCircle size={14} /> : <Circle size={14} />}
          </span>
          <span>{step.title}</span>
        </li>)}</ol>
      </section>}
      {messages.map((message, index) => message.role === "user" ? <UserMessage message={message} attachmentImages={attachmentImages} resourceActions={resourceActions} key={message.id} />
        : message.role === "assistant" && index > lastUserIndex && showRunReply && !runFinished ? null
        : <article className={`message-row ${message.role}`} key={message.id}>
        <div className="message-body">
          <MessageParts parts={message.parts} markdown={message.role === "assistant"} resourceActions={resourceActions} />
          {message.role === "assistant" && <div className="message-actions"><ReplyCopyButton text={message.parts.filter((part) => part.type === "text").map((part) => part.text).join("\n\n")} /></div>}
        </div>
      </article>)}
      {showRunReply && <article className="message-row assistant">
        <div className="message-body">
          {runActive && liveParts.length === 0 && <AssistantWaitingIndicator />}
          <MessageParts parts={liveParts} markdown streaming={runActive} resourceActions={resourceActions} />
          {run.approval && onCancel && onApproval && <RunControls key={runId ?? "approval"} run={run} onCancel={onCancel} onApproval={onApproval} />}
          {runFinished && (runError || run.text.trim()) && <div className="message-actions"><ReplyCopyButton text={runError ?? run.text} /></div>}
        </div>
      </article>}
    </div>}
  </div>;
}
