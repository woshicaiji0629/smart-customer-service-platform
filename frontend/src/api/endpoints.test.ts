import { beforeEach, describe, expect, it, vi } from "vitest";

import { mockLogin } from "./auth";
import { getWithdrawal } from "./business";
import { request } from "./client";
import {
  createConversation,
  getConversation,
  listConversations,
  sendMessage,
} from "./conversations";

vi.mock("./client", () => ({
  request: vi.fn(),
}));

describe("API endpoints", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("创建会话使用 POST", () => {
    void createConversation();

    expect(request).toHaveBeenCalledWith("/conversations", {
      method: "POST",
    });
  });

  it("会话列表正确构造和编码查询参数", () => {
    void listConversations(20, "abc/123");

    expect(request).toHaveBeenCalledWith(
      "/conversations?limit=20&cursor=abc%2F123",
    );
  });

  it("路径参数中的斜杠按单个路径段编码", () => {
    void getConversation("abc/123");
    void getWithdrawal("WD/10001");

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/conversations/abc%2F123",
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/business/withdrawals/WD%2F10001",
    );
  });

  it("发送消息使用编码后的路径和 JSON 请求体", () => {
    void sendMessage("abc/123", "测试问题");

    expect(request).toHaveBeenCalledWith(
      "/conversations/abc%2F123/messages",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: "测试问题" }),
      },
    );
  });

  it("Mock 登录通过 JSON 提交用户 ID", () => {
    void mockLogin("10001");

    expect(request).toHaveBeenCalledWith("/auth/mock-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "10001" }),
    });
  });
});
