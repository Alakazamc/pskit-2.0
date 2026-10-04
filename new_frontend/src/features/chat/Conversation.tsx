import { useId, useLayoutEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronUp, Circle, FlaskConical, LoaderCircle, Sparkles } from "lucide-react";
import type { Message } from "../../api/types";
import type { RunView } from "./events";
import { MessageParts } from "./MessageParts";
import { LazyMarkdownContent } from "./LazyMarkdownContent";
import { ReplyCopyButton } from "./ReplyCopyButton";
import { useLanguage } from "../../i18n/LanguageProvider";
import { localizedRunError } from "./runErrors";
import { RunControls } from "../mono/RunControls";
import { useChatAutoscroll } from "./useChatAutoscroll";

function AssistantResponseHeader({ generating = false }: { generating?: boolean }) {
  const { t } = useLanguage();
  return <div className="assistant-response-header">
    <span className="assistant-response-mark" role={generating ? "status" : undefined}
      aria-label={generating ? t("conversation.generating") : undefined} aria-hidden={generating ? undefined : true}>
      {generating ? <LoaderCircle className="message-spinner" size={20} aria-hidden="true" /> : <Sparkles size={20} aria-hidden="true" />}
    </span>
    <span className="message-author">Research Agent</span>
  </div>;
}

function UserMessage({ message }: { message: Message }) {
  const { t } = useLanguage();
  const [expanded, setExpanded] = useState(false);
  const [canCollapse, setCanCollapse] = useState(false);
  const rowRef = useRef<HTMLElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const pendingAnchor = useRef<{ scroll: HTMLElement; element: HTMLElement; top: number } | null>(null);
  const textId = useId();
  const textParts = message.parts.filter((part) => part.type === "text");
  const otherParts = message.parts.filter((part) => part.type !== "text");

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
    <div className="message-body">
      {textParts.length > 0 && <div id={textId} ref={textRef} className={`user-message-text${expanded ? "" : " is-collapsed"}`}><MessageParts parts={textParts} /></div>}
      <MessageParts parts={otherParts} />
      {canCollapse && <button ref={toggleRef} className="user-message-toggle" type="button" aria-expanded={expanded} aria-controls={textId} onClick={toggle}>
        {expanded ? t("conversation.collapseMessage") : t("conversation.expandMessage")}
        {expanded ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      </button>}
    </div>
  </article>;
}

export function Conversation({ messages, run, runId, awaitingEvents = false, onCancel, onApproval }: { messages: Message[]; run: RunView; runId?: string | null; awaitingEvents?: boolean; onCancel?: () => Promise<void>; onApproval?: (approvalId: string, decision: "approved" | "rejected") => Promise<void> }) {
  const { t } = useLanguage();
  const scrollRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  useChatAutoscroll(scrollRef, listRef, `${messages.length}:${run.text.length}:${run.status}:${run.tools.length}`, runId,
    messages.length > 0 || run.status !== "idle" || awaitingEvents);
  const runError = localizedRunError(run, t);
  const runActive = (run.status === "running" || run.status === "waiting" || awaitingEvents) && !run.approval;
  const lastUserIndex = messages.reduce((last, message, index) => message.role === "user" ? index : last, -1);
  const runStartedAt = run.events.find((event) => event.created_at)?.created_at;
  const savedReplyAvailable = messages.some((message, index) => message.role === "assistant"
    && index > lastUserIndex
    && (!runStartedAt || Date.parse(message.created_at) >= Date.parse(runStartedAt)));
  const showRunReply = (run.status !== "idle" || awaitingEvents) && (run.status !== "completed"
    || (run.text.length > 0 && !savedReplyAvailable));
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
      {messages.map((message) => message.role === "user" ? <UserMessage message={message} key={message.id} /> : <article className={`message-row ${message.role}`} key={message.id}>
        <div className="message-body">
          {message.role === "assistant" && <AssistantResponseHeader />}
          <MessageParts parts={message.parts} markdown={message.role === "assistant"} />
          {message.role === "assistant" && <div className="message-actions"><ReplyCopyButton text={message.parts.filter((part) => part.type === "text").map((part) => part.text).join("\n\n")} /></div>}
        </div>
      </article>)}
      {showRunReply && <article className="message-row assistant">
        <div className="message-body">
          <AssistantResponseHeader generating={runActive} />
          {runError ? <p className="message-text">{runError}</p> : run.text && <LazyMarkdownContent text={run.text} streaming={runActive} />}
          {run.tools.filter((tool) => tool.status === "running").map((tool) => <div className="task-pill" key={tool.id}><LoaderCircle className="message-spinner" size={15} aria-hidden="true" /> {tool.name}</div>)}
          {run.jobId && !["failed", "cancelled"].includes(run.status) && <div className="task-pill"><LoaderCircle className="message-spinner" size={15} aria-hidden="true" /> {run.jobLabel || t("conversation.backgroundTask")}{Number.isFinite(run.progress) && ` · ${Math.round(run.progress)}%`}</div>}
          {run.approval && onCancel && onApproval && <RunControls key={runId ?? "approval"} run={run} onCancel={onCancel} onApproval={onApproval} />}
          <div className="message-actions"><ReplyCopyButton text={runError ?? run.text} /></div>
        </div>
      </article>}
    </div>}
  </div>;
}
