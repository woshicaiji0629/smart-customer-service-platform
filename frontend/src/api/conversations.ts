import { request } from "./client";

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
