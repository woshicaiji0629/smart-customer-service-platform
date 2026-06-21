import {
  type Conversation,
  type ConversationSummary,
  type ConversationTurn,
  createConversation,
  type Message,
  sendMessage,
} from "../api/conversations";

const TITLE_LENGTH = 24;

export function titleFromQuestion(question: string): string {
  const normalized = question.trim().replace(/\s+/g, " ");
  return normalized.length > TITLE_LENGTH
    ? `${normalized.slice(0, TITLE_LENGTH)}…`
    : normalized;
}

export function titleFromMessages(messages: Message[]): string {
  const firstQuestion = messages.find((message) => message.role === "user");
  return firstQuestion ? titleFromQuestion(firstQuestion.content) : "空白会话";
}

export function upsertConversation(
  conversations: ConversationSummary[],
  conversation: ConversationSummary,
): ConversationSummary[] {
  return [
    conversation,
    ...conversations.filter((item) => item.id !== conversation.id),
  ].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function appendConversations(
  conversations: ConversationSummary[],
  additionalConversations: ConversationSummary[],
): ConversationSummary[] {
  const existingIds = new Set(
    conversations.map((conversation) => conversation.id),
  );
  return [
    ...conversations,
    ...additionalConversations.filter(
      (conversation) => !existingIds.has(conversation.id),
    ),
  ];
}

export function createPendingMessage(
  conversationId: string | null,
  content: string,
): Message {
  return {
    id: -Date.now(),
    conversation_id: conversationId ?? "",
    role: "user",
    content,
    sources: [],
    created_at: new Date().toISOString(),
  };
}

interface SendQuestionOptions {
  conversationId: string | null;
  question: string;
  onConversationCreated: (conversation: Conversation) => void;
}

interface SendQuestionResult {
  conversationId: string;
  turn: ConversationTurn;
}

export async function sendQuestion({
  conversationId,
  question,
  onConversationCreated,
}: SendQuestionOptions): Promise<SendQuestionResult> {
  let activeConversationId = conversationId;

  if (!activeConversationId) {
    const conversation = await createConversation();
    activeConversationId = conversation.id;
    // 创建成功后立即同步状态。即使后续发送失败，新会话仍真实存在。
    onConversationCreated(conversation);
  }

  return {
    conversationId: activeConversationId,
    turn: await sendMessage(activeConversationId, question),
  };
}
