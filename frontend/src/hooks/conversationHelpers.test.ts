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
        state: "awaiting_withdrawal_order_id",
        expected_input: "withdrawal_order_id",
        missing_fields: ["order_id"],
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
        state: "awaiting_deposit_txid",
        expected_input: "deposit_txid",
        missing_fields: ["txid"],
        manual_fallback_candidate: false,
      }),
    ]);

    expect(guidance.placeholder).toBe("输入充值 TxID，例如 TX-10001");
  });

  it("优先使用结构化状态切换输入提示", () => {
    const guidance = composerGuidanceFromMessages([
      message("assistant", "请补充信息。", {
        type: "clarify_problem",
        state: "awaiting_deposit_txid",
        expected_input: "problem_description",
        missing_fields: ["txid"],
        manual_fallback_candidate: false,
      }),
    ]);

    expect(guidance.placeholder).toBe("输入充值 TxID，例如 TX-10001");
  });

  it("充值记录未找到时提示补充排查信息", () => {
    const guidance = composerGuidanceFromMessages([
      message("assistant", "未找到当前用户的充值记录 TX-10002。", {
        type: "provide_deposit_followup_details",
        state: "awaiting_deposit_followup_details",
        expected_input: "deposit_followup_details",
        missing_fields: ["coin", "chain", "deposit_time", "page_hint"],
        manual_fallback_candidate: false,
      }),
    ]);

    expect(guidance.placeholder).toBe("补充币种、网络、充值时间和页面提示");
    expect(guidance.guides).toEqual([
      "例如 USDT / TRC20 / 今天 14:30",
      "链上成功但未到账请说明页面提示",
    ]);
  });

  it("提现 pending 进入人工兜底候选时提示补充页面信息", () => {
    const guidance = composerGuidanceFromMessages([
      message("assistant", "该订单仍在平台侧处理中。", {
        type: "provide_withdrawal_review_details",
        state: "manual_fallback_candidate",
        expected_input: "withdrawal_review_details",
        missing_fields: ["page_hint"],
        manual_fallback_candidate: true,
      }),
    ]);

    expect(guidance.placeholder).toBe("补充页面提示、审核状态或限制说明");
    expect(guidance.guides).toEqual([
      "请按页面原文描述提示",
      "不要发送密码或验证码",
    ]);
  });
});
