import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { getConversation, listConversations } from "../api/conversations";
import { useConversationHistory } from "./useConversationHistory";

vi.mock("../api/conversations", () => ({
  getConversation: vi.fn(),
  listConversations: vi.fn(),
}));

const summary = {
  id: "conversation-1",
  title: "历史会话",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:01:00Z",
};
const message = {
  id: 1,
  conversation_id: summary.id,
  role: "user" as const,
  content: "测试问题",
  sources: [],
  created_at: summary.created_at,
};

function installLocalStorage() {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });
}

describe("useConversationHistory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
  });

  it("恢复失败后允许点击当前会话重试", async () => {
    window.localStorage.setItem(
      "smart-support-conversation-id:10001",
      summary.id,
    );
    vi.mocked(listConversations).mockResolvedValue({
      items: [summary],
      next_cursor: null,
    });
    vi.mocked(getConversation)
      .mockRejectedValueOnce(new ApiError(503, "暂时不可用"))
      .mockResolvedValueOnce({ ...summary, messages: [message] });

    const { result } = renderHook(() => useConversationHistory("10001"));
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    expect(result.current.listError).toBe("暂时不可用");
    expect(result.current.conversationId).toBe(summary.id);

    await act(() => result.current.selectConversation(summary.id));
    expect(result.current.messages).toEqual([message]);
    expect(result.current.listError).toBeNull();
    expect(getConversation).toHaveBeenCalledTimes(2);
  });

  it("切换用户后忽略旧用户延迟返回的历史记录", async () => {
    let resolveOldHistory!: (history: { messages: typeof message[] } & typeof summary) => void;
    const oldHistory = new Promise<{ messages: typeof message[] } & typeof summary>(
      (resolve) => { resolveOldHistory = resolve; },
    );
    const secondSummary = { ...summary, id: "conversation-2", title: "用户二" };
    const secondMessage = { ...message, id: 2, conversation_id: secondSummary.id };
    window.localStorage.setItem("smart-support-conversation-id:10001", summary.id);
    window.localStorage.setItem("smart-support-conversation-id:10002", secondSummary.id);
    vi.mocked(listConversations)
      .mockResolvedValueOnce({ items: [summary], next_cursor: null })
      .mockResolvedValueOnce({ items: [secondSummary], next_cursor: null });
    vi.mocked(getConversation).mockImplementation((conversationId) =>
      conversationId === summary.id
        ? oldHistory
        : Promise.resolve({ ...secondSummary, messages: [secondMessage] }),
    );

    const { result, rerender } = renderHook(
      ({ userId }) => useConversationHistory(userId),
      { initialProps: { userId: "10001" } },
    );
    await waitFor(() => expect(getConversation).toHaveBeenCalledWith(summary.id));
    rerender({ userId: "10002" });
    await waitFor(() => expect(result.current.conversationId).toBe(secondSummary.id));

    await act(async () => resolveOldHistory({ ...summary, messages: [message] }));
    expect(result.current.conversationId).toBe(secondSummary.id);
    expect(result.current.messages).toEqual([secondMessage]);
  });
});
