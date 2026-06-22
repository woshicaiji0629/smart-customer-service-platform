import { describe, expect, it } from "vitest";

import type { Message, NextAction } from "../api/conversations";
import { composerGuidanceFromMessages } from "./conversationHelpers";

function message(
  role: Message["role"],
  content: string,
  nextAction?: NextAction,
): Message {
  return {
    id: 1,
    conversation_id: "conversation-1",
    role,
    content,
    sources: [],
    created_at: "2026-01-01T00:00:00Z",
    next_action: nextAction,
  };
}

describe("composerGuidanceFromMessages", () => {
  it("默认提示用户补充关键信息", () => {
    const guidance = composerGuidanceFromMessages([]);

    expect(guidance.placeholder).toBe(
      "描述问题，或输入 WD-10001 / TX-10001 查询进度…",
    );
    expect(guidance.guides).toContain("有订单号请一并发送");
  });

  it("提现订单号待补充时切换为订单号提示", () => {
    const guidance = composerGuidanceFromMessages([
      message("assistant", "请提供提现订单号，例如 WD-10001，我可以帮你查询处理状态。"),
    ]);

    expect(guidance.placeholder).toBe("输入提现订单号，例如 WD-10001");
    expect(guidance.guides).toEqual([
      "发送订单号即可继续查询",
      "订单号通常以 WD- 开头",
    ]);
  });

  it("优先使用结构化下一步动作切换为订单号提示", () => {
    const guidance = composerGuidanceFromMessages([
      message("assistant", "请补充信息。", {
        type: "provide_withdrawal_order_id",
        expected_input: "withdrawal_order_id",
        manual_fallback_candidate: false,
      }),
    ]);

    expect(guidance.placeholder).toBe("输入提现订单号，例如 WD-10001");
  });

  it("充值 TxID 待补充时切换为 TxID 提示", () => {
    const guidance = composerGuidanceFromMessages([
      message("assistant", "请提供充值 TxID，例如 TX-10001，我可以帮你查询充值处理状态。"),
    ]);

    expect(guidance.placeholder).toBe("输入充值 TxID，例如 TX-10001");
    expect(guidance.guides).toEqual([
      "发送 TxID 即可继续查询",
      "TxID 通常以 TX- 开头",
    ]);
  });

  it("结构化下一步动作可切换为 TxID 提示", () => {
    const guidance = composerGuidanceFromMessages([
      message("assistant", "请补充信息。", {
        type: "provide_deposit_txid",
        expected_input: "deposit_txid",
        manual_fallback_candidate: false,
      }),
    ]);

    expect(guidance.placeholder).toBe("输入充值 TxID，例如 TX-10001");
  });
});
