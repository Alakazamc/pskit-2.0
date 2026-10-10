import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import type { Message, MessageRequest, ResearchApi, UserIdentity } from "../../api/types";
import { ApiError } from "../../api/http";
import { errorTranslationKey } from "../../i18n/errors";
import { useLanguage } from "../../i18n/LanguageProvider";
import { completeComposerDraft, conversationDraftScope, moveComposerDraft, readComposerDraft, type ComposerDraftScope } from "../chat/composerDrafts";
import { acceptPendingMessage } from "../chat/useMessageHistory";
import { sessionPath } from "./sessionPaths";

export function useConversationSend(api: ResearchApi, user: UserIdentity, activeSessionId: string | null,
  activeProjectId: string | null, personalProjectId: string, draftScope: ComposerDraftScope) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { t } = useLanguage();
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [showUpgradePrompt, setShowUpgradePrompt] = useState(false);
  const [currentRun, setCurrentRun] = useState<{ sessionId: string; runId: string } | null>(null);
  useEffect(() => {
    if (currentRun && currentRun.sessionId !== activeSessionId) setCurrentRun(null);
  }, [activeSessionId, currentRun]);
  const sendGuard = useRef(false);
  const pendingSend = useRef<{ sessionId: string; body: string; key: string } | null>(null);
  const send = async (payload: MessageRequest) => {
    if (sendGuard.current || !payload.content.trim()) return false;
    sendGuard.current = true;
    setSending(true);
    setSubmitting(true);
    setSendError(null);
    setShowUpgradePrompt(false);
    let optimistic: { queryKey: string[]; id: string } | undefined;
    const submittedDraft = readComposerDraft(draftScope);
    try {
      const targetProjectId = activeProjectId ?? personalProjectId;
      let targetSessionId = activeSessionId;
      let createdSession = false;
      if (!targetSessionId) {
        const title = payload.content.trim().replace(/\s+/g, " ").slice(0, 48);
        const created = await api.createSession(activeProjectId, title, true);
        targetSessionId = created.id;
        createdSession = true;
        moveComposerDraft(draftScope, conversationDraftScope(user.id, created.id));
        queryClient.setQueryData(["session", user.id, created.id], created);
      }
      const body = JSON.stringify(payload);
      if (pendingSend.current?.sessionId !== targetSessionId || pendingSend.current.body !== body) {
        pendingSend.current = { sessionId: targetSessionId, body, key: crypto.randomUUID() };
      }
      const queryKey = ["messages", user.id, targetSessionId];
      await queryClient.cancelQueries({ queryKey });
      const message: Message = {
        id: `pending-${pendingSend.current.key}`, session_id: targetSessionId, role: "user",
        created_at: new Date().toISOString(),
        parts: [{ type: "text", text: payload.content },
          ...payload.attachments.map((file) => ({ type: "file" as const, id: file.id, name: file.name }))],
      };
      optimistic = { queryKey, id: message.id };
      queryClient.setQueryData<Message[]>(queryKey, (previous = []) => [...previous, message]);
      if (createdSession) navigate(sessionPath(targetProjectId, personalProjectId, targetSessionId));
      const result = await api.sendMessage(targetSessionId, payload, pendingSend.current.key, activeProjectId);
      if (createdSession) completeComposerDraft(conversationDraftScope(user.id, targetSessionId), submittedDraft);
      if (optimistic) acceptPendingMessage(queryClient, user.id, targetSessionId, optimistic.id);
      pendingSend.current = null;

      // CRITICAL: Start event polling immediately, before waiting for message refresh
      // This reduces first-token latency from ~5s to ~1.5s
      setCurrentRun({ sessionId: targetSessionId, runId: result.run_id });
      setSubmitting(false);

      // Refresh messages in background without blocking event stream
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["messages", user.id, targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions", user.id, targetProjectId] }),
        queryClient.invalidateQueries({ queryKey: ["usage", user.id] }),
        queryClient.invalidateQueries({ queryKey: ["usage-entries", user.id] }),
      ]);
      return true;
    } catch (caught) {
      if (optimistic) {
        const id = optimistic.id;
        queryClient.setQueryData<Message[]>(optimistic.queryKey, (previous) => previous?.filter((message) => message.id !== id));
      }
      const key = errorTranslationKey(caught);
      setSendError(key ? t(key) : t("workspace.sendFailed"));
      setShowUpgradePrompt(Boolean(user.is_anonymous && caught instanceof ApiError
        && ["TOKEN_QUOTA_EXCEEDED", "LOGIN_REQUIRED"].includes(caught.code ?? "")));
      return false;
    } finally {
      setSubmitting(false);
      setSending(false);
      sendGuard.current = false;
    }
  };
  const upload = async (file: File, onProgress: (value: number) => void) => {
    const ref = await api.uploadFile(file);
    onProgress(100);
    await queryClient.invalidateQueries({ queryKey: ["files", user.id] });
    return ref;
  };
  return { sending, submitting, sendError, showUpgradePrompt, currentRun, send, upload };
}
