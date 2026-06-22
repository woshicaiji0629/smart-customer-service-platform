import {
  type Conversation,
  type ConversationSummary,
  type ConversationTurn,
  createConversation,
  type Message,
  sendMessage,
} from "../api/conversations";

const TITLE_LENGTH = 24;
const WITHDRAWAL_ORDER_PROMPT = "请提供提现订单号";
const DEPOSIT_TXID_PROMPT = "请提供充值 TxID";

export interface ComposerGuidance {
  placeholder: string;
  guides: string[];
}

const DEFAULT_COMPOSER_GUIDANCE: ComposerGuidance = {
  placeholder: "描述问题，或输入 WD-10001 / TX-10001 查询进度…",
  guides: [
    "有订单号请一并发送",
    "充值问题可提供 TxID",
    "认证失败请说明页面提示",
  ],
};

const WITHDRAWAL_ORDER_GUIDANCE: ComposerGuidance = {
  placeholder: "输入提现订单号，例如 WD-10001",
  guides: ["发送订单号即可继续查询", "订单号通常以 WD- 开头"],
};

const DEPOSIT_TXID_GUIDANCE: ComposerGuidance = {
  placeholder: "输入充值 TxID，例如 TX-10001",
  guides: ["发送 TxID 即可继续查询", "TxID 通常以 TX- 开头"],
};

const DEPOSIT_FOLLOWUP_GUIDANCE: ComposerGuidance = {
  placeholder: "补充币种、网络、充值时间和页面提示",
  guides: ["例如 USDT / TRC20 / 今天 14:30", "链上成功但未到账请说明页面提示"],
};

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

export function composerGuidanceFromMessages(
  messages: Message[],
): ComposerGuidance {
  const lastAssistantMessage = [...messages]
    .reverse()
    .find((message) => message.role === "assistant");
  if (!lastAssistantMessage) {
    return DEFAULT_COMPOSER_GUIDANCE;
  }
  if (
    lastAssistantMessage.next_action?.state === "awaiting_withdrawal_order_id"
  ) {
    return WITHDRAWAL_ORDER_GUIDANCE;
  }
  if (lastAssistantMessage.next_action?.state === "awaiting_deposit_txid") {
    return DEPOSIT_TXID_GUIDANCE;
  }
  if (
    lastAssistantMessage.next_action?.state ===
    "awaiting_deposit_followup_details"
  ) {
    return DEPOSIT_FOLLOWUP_GUIDANCE;
  }
  if (
    lastAssistantMessage.next_action?.expected_input === "withdrawal_order_id"
  ) {
    return WITHDRAWAL_ORDER_GUIDANCE;
  }
  if (lastAssistantMessage.next_action?.expected_input === "deposit_txid") {
    return DEPOSIT_TXID_GUIDANCE;
  }
  if (
    lastAssistantMessage.next_action?.expected_input ===
    "deposit_followup_details"
  ) {
    return DEPOSIT_FOLLOWUP_GUIDANCE;
  }
  if (lastAssistantMessage.content.includes(WITHDRAWAL_ORDER_PROMPT)) {
    return WITHDRAWAL_ORDER_GUIDANCE;
  }
  if (lastAssistantMessage.content.includes(DEPOSIT_TXID_PROMPT)) {
    return DEPOSIT_TXID_GUIDANCE;
  }
  return DEFAULT_COMPOSER_GUIDANCE;
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
