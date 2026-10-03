import { useId, useLayoutEffect, useRef, useState } from "react";
import { Bot, Check, ChevronDown, ChevronUp, Circle, FlaskConical, LoaderCircle, Square } from "lucide-react";
import type { Message } from "../../api/types";
import type { RunView } from "./events";
import { MessageParts } from "./MessageParts";
import { LazyMarkdownContent } from "./LazyMarkdownContent";
import { ReplyCopyButton } from "./ReplyCopyButton";
import { useLanguage } from "../../i18n/LanguageProvider";
import { localizedRunError } from "./runErrors";
import { errorTranslationKey } from "../../i18n/errors";

function StopRunButton({ onCancel }: { onCancel: () => Promise<void> }) {
  const { t } = useLanguage();
  const [busy, setBusy] = useState(false);
  const [requested, setRequested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stop = async () => {
    setBusy(true);
    setError(null);
    try {
      await onCancel();
      setRequested(true);
    } catch (caught) {
      const key = errorTranslationKey(caught);
      setError(key ? t(key) : t("workspace.cancelFailed"));
    } finally {
      setBusy(false);
    }
  };
  return <><button type="button" className="message-stop-button" aria-label={t("agent.cancel")} title={t("agent.cancel")} disabled={busy || requested} onClick={() => void stop()}><Square size={14} fill="currentColor" aria-hidden="true" /></button>{error && <span className="message-stop-error" role="alert">{error}</span>}</>;
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

export function Conversation({ messages, run, runId, onCancel }: { messages: Message[]; run: RunView; runId?: string | null; onCancel?: () => Promise<void> }) {
  const { t } = useLanguage();
  const runError = localizedRunError(run, t);
  const runActive = (run.status === "running" || run.status === "waiting") && !run.approval;
  return <div className="conversation-scroll">
    {messages.length === 0 && run.status === "idle" ? <div className="empty-chat">
      <div className="empty-icon"><FlaskConical size={28} /></div>
      <span className="eyebrow">{t("workspace.eyebrow")}</span>
      <h2>{t("conversation.emptyTitle")}</h2>
      <p>{t("conversation.emptyDescription")}</p>
    </div> : <div className="message-list">
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
        {message.role === "assistant" && <div className="assistant-avatar"><Bot size={17} /></div>}
        <div className="message-body">{message.role === "assistant" && <span className="message-author">Research Agent</span>}<MessageParts parts={message.parts} markdown={message.role === "assistant"} />{message.role === "assistant" && <div className="message-actions"><ReplyCopyButton text={message.parts.filter((part) => part.type === "text").map((part) => part.text).join("\n\n")} /></div>}</div>
      </article>)}
      {run.status !== "idle" && run.status !== "completed" && <article className="message-row assistant"><div className="assistant-avatar"><Bot size={17} /></div><div className="message-body"><span className="message-author">Research Agent</span>{runError ? <p className="message-text">{runError}</p> : run.text && <LazyMarkdownContent text={run.text} streaming={runActive} />}{run.tools.filter((tool) => tool.status === "running").map((tool) => <div className="task-pill" key={tool.id}><LoaderCircle className="message-spinner" size={15} aria-hidden="true" /> {tool.name}</div>)}{run.jobId && !["failed", "cancelled"].includes(run.status) && <div className="task-pill"><LoaderCircle className="message-spinner" size={15} aria-hidden="true" /> {run.jobLabel || t("conversation.backgroundTask")}{Number.isFinite(run.progress) && ` · ${Math.round(run.progress)}%`}</div>}<div className="message-actions"><ReplyCopyButton text={runError ?? run.text} />{runActive && <span className="message-generating" role="status" aria-label={t("conversation.generating")}><LoaderCircle className="message-spinner" size={16} aria-hidden="true" /></span>}{runActive && onCancel && <StopRunButton key={runId ?? "active"} onCancel={onCancel} />}</div></div></article>}
    </div>}
  </div>;
}
