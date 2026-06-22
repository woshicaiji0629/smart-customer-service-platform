import type { SubmitEvent } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import {
  createConversation,
  getConversation,
  listConversations,
  sendMessage,
} from "../api/conversations";
import { useConversations } from "./useConversations";

vi.mock("../api/conversations", () => ({
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  sendMessage: vi.fn(),
}));

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

describe("useConversations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
  });

  it("发送失败时移除临时消息并恢复输入内容", async () => {
    vi.mocked(listConversations).mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(createConversation).mockResolvedValue({
      id: "conversation-1",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(sendMessage).mockRejectedValue(new ApiError(503, "发送失败"));
    vi.mocked(getConversation).mockRejectedValue(new Error("不应调用"));

    const { result } = renderHook(() => useConversations("10001"));
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    act(() => result.current.setInput("  请查询提现  "));
    const event = {
      preventDefault: vi.fn(),
    } as unknown as SubmitEvent<HTMLFormElement>;

    await act(() => result.current.submit(event));
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(result.current.messages).toEqual([]);
    expect(result.current.input).toBe("请查询提现");
    expect(result.current.error).toBe("发送失败");
    expect(result.current.isSending).toBe(false);
  });

  it("可以直接发送快捷问题而不写入输入框", async () => {
    vi.mocked(listConversations).mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(createConversation).mockResolvedValue({
      id: "conversation-1",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
    vi.mocked(sendMessage).mockResolvedValue({
      user_message: {
        id: 1,
        conversation_id: "conversation-1",
        role: "user",
        content: "提现完成但钱包没到账怎么办？",
        sources: [],
        created_at: "2026-01-01T00:00:01Z",
      },
      assistant_message: {
        id: 2,
        conversation_id: "conversation-1",
        role: "assistant",
        content: "请提供提现订单号。",
        sources: [],
        created_at: "2026-01-01T00:00:02Z",
      },
    });
    vi.mocked(getConversation).mockRejectedValue(new Error("不应调用"));

    const { result } = renderHook(() => useConversations("10001"));
    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));

    await act(() =>
      result.current.submitQuestion("提现完成但钱包没到账怎么办？"),
    );

    expect(createConversation).toHaveBeenCalledOnce();
    expect(sendMessage).toHaveBeenCalledWith(
      "conversation-1",
      "提现完成但钱包没到账怎么办？",
    );
    expect(result.current.input).toBe("");
    expect(result.current.messages.map((message) => message.content)).toEqual([
      "提现完成但钱包没到账怎么办？",
      "请提供提现订单号。",
    ]);
  });
});
