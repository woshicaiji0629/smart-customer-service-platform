import type { SubmitEvent } from "react";
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getWithdrawal } from "../api/business";
import { ApiError } from "../api/client";
import { useWithdrawal } from "./useWithdrawal";

vi.mock("../api/business", () => ({ getWithdrawal: vi.fn() }));

const event = {
  preventDefault: vi.fn(),
} as unknown as SubmitEvent<HTMLFormElement>;

describe("useWithdrawal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("查询失败时显示接口错误", async () => {
    vi.mocked(getWithdrawal).mockRejectedValue(new ApiError(404, "订单不存在"));
    const { result } = renderHook(() => useWithdrawal("10001"));
    act(() => result.current.setOrderId(" WD-404 "));

    await act(() => result.current.query(event));
    expect(getWithdrawal).toHaveBeenCalledWith("WD-404");
    expect(result.current.withdrawal).toBeNull();
    expect(result.current.error).toBe("订单不存在");
  });

  it("切换用户后忽略旧用户延迟返回的查询结果", async () => {
    let resolveWithdrawal!: (value: {
      order_id: string;
      coin: string;
      size: string;
      status: "success";
      chain: string;
      updated_at: string;
    }) => void;
    vi.mocked(getWithdrawal).mockReturnValue(new Promise((resolve) => {
      resolveWithdrawal = resolve;
    }));
    const { result, rerender } = renderHook(
      ({ userId }) => useWithdrawal(userId),
      { initialProps: { userId: "10001" } },
    );
    act(() => result.current.setOrderId("WD-10001"));
    let request!: Promise<void>;
    act(() => { request = result.current.query(event); });

    rerender({ userId: "10002" });
    await act(async () => {
      resolveWithdrawal({
        order_id: "WD-10001",
        coin: "USDT",
        size: "10",
        status: "success",
        chain: "TRON",
        updated_at: "2026-01-01T00:00:00Z",
      });
      await request;
    });
    expect(result.current.orderId).toBe("");
    expect(result.current.withdrawal).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });
});
