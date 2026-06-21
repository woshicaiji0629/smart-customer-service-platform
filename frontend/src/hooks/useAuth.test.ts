import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getCurrentUser,
  listMockUsers,
  logout,
  mockLogin,
} from "../api/auth";
import { ApiError } from "../api/client";
import { useAuth } from "./useAuth";

vi.mock("../api/auth", () => ({
  getCurrentUser: vi.fn(),
  listMockUsers: vi.fn(),
  logout: vi.fn(),
  mockLogin: vi.fn(),
}));

const alice = { user_id: "10001", display_name: "Alice" };

describe("useAuth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("未登录时加载 Mock 用户，并支持登录和退出", async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(new ApiError(401, "未登录"));
    vi.mocked(listMockUsers).mockResolvedValue([alice]);
    vi.mocked(mockLogin).mockResolvedValue(alice);
    vi.mocked(logout).mockResolvedValue(undefined);

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.mockUsers).toEqual([alice]);
    expect(result.current.error).toBeNull();

    await act(() => result.current.login(alice.user_id));
    expect(result.current.user).toEqual(alice);

    await act(() => result.current.logout());
    expect(result.current.user).toBeNull();
  });

  it("登录失败时保留未登录状态并显示错误", async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(new ApiError(401, "未登录"));
    vi.mocked(listMockUsers).mockResolvedValue([alice]);
    vi.mocked(mockLogin).mockRejectedValue(new ApiError(503, "服务不可用"));

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(() => result.current.login(alice.user_id));
    expect(result.current.user).toBeNull();
    expect(result.current.error).toBe("服务不可用");
  });
});
