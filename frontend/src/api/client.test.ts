import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, request } from "./client";

function response(options: {
  status: number;
  body?: unknown;
  jsonError?: unknown;
}): Response {
  return {
    ok: options.status >= 200 && options.status < 300,
    status: options.status,
    json: options.jsonError
      ? vi.fn().mockRejectedValue(options.jsonError)
      : vi.fn().mockResolvedValue(options.body),
  } as unknown as Response;
}

describe("API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("未指定 method 时交给 Fetch 使用默认 GET", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ status: 200, body: { status: "ok" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(request<{ status: string }>("/health")).resolves.toEqual({
      status: "ok",
    });
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/health", {
      credentials: "include",
    });
  });

  it.each([401, 404, 503])("保留服务端 %i 错误信息", async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({ status, body: { detail: `服务错误 ${status}` } }),
      ),
    );

    const error = await request("/failure").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, message: `服务错误 ${status}` });
  });

  it("错误响应不是 JSON 时使用包含状态码的默认提示", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({ status: 500, jsonError: new SyntaxError("invalid json") }),
      ),
    );

    await expect(request("/failure")).rejects.toMatchObject({
      status: 500,
      message: "请求失败（500）",
    });
  });

  it("204 响应不解析 JSON 并返回 undefined", async () => {
    const noContentResponse = response({ status: 204 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(noContentResponse));

    await expect(request("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
    expect(noContentResponse.json).not.toHaveBeenCalled();
  });
});
