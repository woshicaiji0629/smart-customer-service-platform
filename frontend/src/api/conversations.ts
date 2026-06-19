const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
).replace(/\/$/, "");

export type MessageRole = "user" | "assistant";

export interface SourceSnapshot {
  article_id: string;
  title: string;
  source_url: string;
}

export interface Message {
  id: number;
  conversation_id: string;
  role: MessageRole;
  content: string;
  sources: SourceSnapshot[];
  created_at: string;
}

export interface Conversation {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationHistory extends Conversation {
  messages: Message[];
}

export interface ConversationTurn {
  user_message: Message;
  assistant_message: Message;
}

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, init);

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

  return (await response.json()) as T;
}

export function createConversation(): Promise<Conversation> {
  return request<Conversation>("/conversations", { method: "POST" });
}

export function getConversation(
  conversationId: string,
): Promise<ConversationHistory> {
  return request<ConversationHistory>(
    `/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export function sendMessage(
  conversationId: string,
  content: string,
): Promise<ConversationTurn> {
  return request<ConversationTurn>(
    `/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
}
