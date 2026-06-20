const DEFAULT_API_BASE_URL = "http://localhost:8000";

const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
).replace(/\/$/, "");

interface ApiErrorBody {
  detail?: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: "include",
  });

  if (!response.ok) {
    let message = `请求失败（${response.status}）`;

    try {
      const body = (await response.json()) as ApiErrorBody;
      if (body.detail) {
        message = body.detail;
      }
    } catch {
      // The fallback message already contains the HTTP status.
    }

    throw new ApiError(response.status, message);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
