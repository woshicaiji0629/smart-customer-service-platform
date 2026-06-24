import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiError, getErrorMessage } from "../api/client";
import {
  type ConversationSummary,
  getConversation,
  listConversations,
  type Message,
} from "../api/conversations";
import {
  appendConversations,
  titleFromMessages,
  upsertConversation,
} from "./conversationHelpers";

const STORAGE_KEY_PREFIX = "smart-support-conversation-id";
const PAGE_SIZE = 20;

function userStorageKey(userId: string): string {
  return `${STORAGE_KEY_PREFIX}:${userId}`;
}

export function useConversationHistory(userId: string | null) {
  const requestVersionRef = useRef(0);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasLoadedStorage, setHasLoadedStorage] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function handleRestoreError(
    requestError: unknown,
    storedConversationId: string | null,
  ) {
    if (
      storedConversationId &&
      requestError instanceof ApiError &&
      requestError.status === 404
    ) {
      setConversationId(null);
      setConversations((current) =>
        current.filter((item) => item.id !== storedConversationId),
      );
      return;
    }
    setListError(getErrorMessage(requestError));
  }

  // 在浏览器绘制新用户界面前使旧请求失效，避免旧结果短暂写入新用户状态。
  useLayoutEffect(() => {
    requestVersionRef.current += 1;
    return () => {
      requestVersionRef.current += 1;
    };
  }, [userId]);

  // 用户改变时先清空旧数据，再恢复该用户的列表及上次打开的会话。
  useEffect(() => {
    if (!userId) {
      setConversationId(null);
      setConversations([]);
      setNextCursor(null);
      setIsLoadingMore(false);
      setListError(null);
      setMessages([]);
      setHasLoadedStorage(false);
      setIsLoadingHistory(false);
      setError(null);
      return;
    }

    const activeUserId = userId;
    let isActive = true;
    setHasLoadedStorage(false);
    setIsLoadingHistory(true);
    setConversationId(null);
    setConversations([]);
    setNextCursor(null);
    setIsLoadingMore(false);
    setListError(null);
    setMessages([]);
    setError(null);

    async function restoreConversation() {
      let storedConversationId: string | null = null;

      try {
        storedConversationId = window.localStorage.getItem(
          userStorageKey(activeUserId),
        );
      } catch {
        if (isActive) {
          setError("无法读取浏览器中的当前会话。");
        }
      }

      try {
        const result = await listConversations(PAGE_SIZE, null);
        if (!isActive) {
          return;
        }
        setConversations(result.items);
        setNextCursor(result.next_cursor);

        if (!storedConversationId) {
          setConversationId(null);
          return;
        }

        setConversationId(storedConversationId);
        const history = await getConversation(storedConversationId);
        if (isActive) {
          setMessages(history.messages);
          if (!result.items.some((item) => item.id === history.id)) {
            setConversations((current) =>
              upsertConversation(current, {
                id: history.id,
                title: titleFromMessages(history.messages),
                created_at: history.created_at,
                updated_at: history.updated_at,
              }),
            );
          }
        }
      } catch (requestError) {
        if (isActive) {
          handleRestoreError(requestError, storedConversationId);
        }
      } finally {
        if (isActive) {
          setHasLoadedStorage(true);
          setIsLoadingHistory(false);
        }
      }
    }

    void restoreConversation();
    return () => {
      // 退出、切换用户或组件卸载后，忽略延迟返回的异步结果。
      isActive = false;
    };
  }, [userId]);

  // 首次恢复完成后才能写入，否则初始空状态可能提前删除已保存的会话。
  useEffect(() => {
    if (!hasLoadedStorage || !userId) {
      return;
    }

    try {
      if (conversationId) {
        window.localStorage.setItem(
          userStorageKey(userId),
          conversationId,
        );
      } else {
        window.localStorage.removeItem(userStorageKey(userId));
      }
    } catch {
      setError("无法在浏览器中保存当前会话。");
    }
  }, [conversationId, hasLoadedStorage, userId]);

  function startNewConversation() {
    setConversationId(null);
    setMessages([]);
    setError(null);
  }

  function createRequestScope(): () => boolean {
    const requestVersion = requestVersionRef.current;
    return () => requestVersionRef.current === requestVersion;
  }

  async function loadMore() {
    if (isLoadingMore || nextCursor === null) {
      return;
    }

    const isRequestActive = createRequestScope();
    setIsLoadingMore(true);
    setListError(null);
    try {
      const result = await listConversations(PAGE_SIZE, nextCursor);
      if (!isRequestActive()) {
        return;
      }
      setConversations((current) => appendConversations(current, result.items));
      setNextCursor(result.next_cursor);
    } catch (requestError) {
      if (isRequestActive()) {
        setListError(getErrorMessage(requestError));
      }
    } finally {
      if (isRequestActive()) {
        setIsLoadingMore(false);
      }
    }
  }

  async function selectConversation(selectedConversationId: string) {
    const isCurrentConversationLoaded =
      selectedConversationId === conversationId && messages.length > 0;
    if (
      isCurrentConversationLoaded ||
      isLoadingHistory
    ) {
      return;
    }

    const isRequestActive = createRequestScope();
    setError(null);
    setListError(null);
    setIsLoadingHistory(true);
    try {
      const history = await getConversation(selectedConversationId);
      if (!isRequestActive()) {
        return;
      }
      setConversationId(history.id);
      setMessages(history.messages);
    } catch (requestError) {
      if (!isRequestActive()) {
        return;
      }
      if (requestError instanceof ApiError && requestError.status === 404) {
        setConversations((current) =>
          current.filter((item) => item.id !== selectedConversationId),
        );
        setError("该历史会话已不存在。");
      } else {
        setError(getErrorMessage(requestError));
      }
    } finally {
      if (isRequestActive()) {
        setIsLoadingHistory(false);
      }
    }
  }

  return {
    conversationId,
    conversations,
    nextCursor,
    isLoadingMore,
    listError,
    messages,
    isLoadingHistory,
    error,
    startNewConversation,
    loadMore,
    selectConversation,
    createRequestScope,
    clearError: () => setError(null),
    mutations: {
      setConversationId,
      setConversations,
      setMessages,
      setError,
    },
  };
}
