import { type SubmitEvent, useLayoutEffect, useState } from "react";

import { getErrorMessage } from "../api/client";
import type {
  Conversation,
  ConversationTurn,
  Message,
} from "../api/conversations";
import {
  createPendingMessage,
  sendQuestion,
  titleFromQuestion,
  upsertConversation,
} from "./conversationHelpers";
import { useConversationHistory } from "./useConversationHistory";

export function useConversations(userId: string | null) {
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const history = useConversationHistory(userId);

  // 旧用户的请求会被版本机制忽略；在新用户界面绘制前清理草稿和错误。
  useLayoutEffect(() => {
    setInput("");
    setIsSending(false);
    history.clearError();
  }, [userId]);

  function startNewConversation() {
    if (history.isLoadingHistory || isSending) {
      return;
    }
    history.startNewConversation();
    setInput("");
  }

  async function selectConversation(conversationId: string) {
    if (isSending) {
      return;
    }
    await history.selectConversation(conversationId);
  }

  function beginSubmission(question: string): Message {
    const pendingMessage = createPendingMessage(
      history.conversationId,
      question,
    );
    setInput("");
    history.mutations.setError(null);
    setIsSending(true);
    history.mutations.setMessages((current) => [...current, pendingMessage]);
    return pendingMessage;
  }

  function addCreatedConversation(
    conversation: Conversation,
    question: string,
  ) {
    history.mutations.setConversationId(conversation.id);
    history.mutations.setConversations((current) =>
      upsertConversation(current, {
        ...conversation,
        title: titleFromQuestion(question),
      }),
    );
  }

  function commitTurn(
    pendingMessage: Message,
    question: string,
    conversationId: string,
    turn: ConversationTurn,
  ) {
    history.mutations.setMessages((current) => [
      ...current.filter((message) => message.id !== pendingMessage.id),
      turn.user_message,
      turn.assistant_message,
    ]);
    history.mutations.setConversations((current) => {
      const existing = current.find(
        (conversation) => conversation.id === conversationId,
      );
      return upsertConversation(current, {
        id: conversationId,
        title: existing?.title ?? titleFromQuestion(question),
        created_at: existing?.created_at ?? turn.user_message.created_at,
        updated_at: turn.assistant_message.created_at,
      });
    });
  }

  function rollbackSubmission(
    pendingMessage: Message,
    question: string,
    requestError: unknown,
  ) {
    history.mutations.setMessages((current) =>
      current.filter((message) => message.id !== pendingMessage.id),
    );
    setInput(question);
    history.mutations.setError(getErrorMessage(requestError));
  }

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = input.trim();
    if (!question || isSending) {
      return;
    }

    // 先立即显示用户问题，请求完成后再用服务端记录替换。
    const isRequestActive = history.createRequestScope();
    const pendingMessage = beginSubmission(question);

    try {
      const result = await sendQuestion({
        conversationId: history.conversationId,
        question,
        onConversationCreated: (conversation) => {
          if (isRequestActive()) {
            addCreatedConversation(conversation, question);
          }
        },
      });
      if (!isRequestActive()) {
        return;
      }
      commitTurn(
        pendingMessage,
        question,
        result.conversationId,
        result.turn,
      );
    } catch (requestError) {
      if (!isRequestActive()) {
        return;
      }
      // 请求失败时回滚临时消息并恢复输入，用户无需重新输入即可重试。
      rollbackSubmission(pendingMessage, question, requestError);
    } finally {
      if (isRequestActive()) {
        setIsSending(false);
      }
    }
  }

  return {
    conversationId: history.conversationId,
    conversations: history.conversations,
    nextCursor: history.nextCursor,
    isLoadingMore: history.isLoadingMore,
    listError: history.listError,
    messages: history.messages,
    input,
    isLoadingHistory: history.isLoadingHistory,
    isSending,
    error: history.error,
    setInput,
    clearError: history.clearError,
    startNewConversation,
    loadMore: history.loadMore,
    selectConversation,
    submit,
  };
}
