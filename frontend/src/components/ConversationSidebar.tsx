import type { SubmitEvent } from "react";

import type { AuthenticatedUser } from "../api/auth";
import type { Withdrawal } from "../api/business";
import type { ConversationSummary } from "../api/conversations";

interface ConversationSidebarProps {
  user: AuthenticatedUser;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  nextCursor: string | null;
  isLoadingHistory: boolean;
  isSending: boolean;
  isLoadingMore: boolean;
  listError: string | null;
  withdrawalOrderId: string;
  withdrawal: Withdrawal | null;
  isLoadingWithdrawal: boolean;
  onNewConversation: () => void;
  onLoadMore: () => void;
  onSelectConversation: (conversationId: string) => void;
  onWithdrawalOrderIdChange: (value: string) => void;
  onWithdrawalQuery: (event: SubmitEvent<HTMLFormElement>) => void;
}

function formatUpdatedAt(updatedAt: string): string {
  const date = new Date(updatedAt);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ConversationSidebar({
  user,
  conversations,
  activeConversationId,
  nextCursor,
  isLoadingHistory,
  isSending,
  isLoadingMore,
  listError,
  withdrawalOrderId,
  withdrawal,
  isLoadingWithdrawal,
  onNewConversation,
  onLoadMore,
  onSelectConversation,
  onWithdrawalOrderIdChange,
  onWithdrawalQuery,
}: ConversationSidebarProps) {
  return (
    <aside className="conversation-sidebar" aria-label="会话管理">
      <div className="sidebar-header">
        <div>
          <p className="sidebar-eyebrow">CONVERSATIONS</p>
          <h2>会话</h2>
        </div>
        <button
          type="button"
          onClick={onNewConversation}
          disabled={isLoadingHistory || isSending}
        >
          新建会话
        </button>
      </div>

      {conversations.length === 0 ? (
        <p className="conversation-empty">暂无历史会话</p>
      ) : (
        <>
          <nav className="conversation-list" aria-label="历史会话">
            {conversations.map((conversation) => (
              <button
                className={
                  conversation.id === activeConversationId
                    ? "is-active"
                    : undefined
                }
                type="button"
                key={conversation.id}
                onClick={() => onSelectConversation(conversation.id)}
                disabled={isLoadingHistory || isSending}
              >
                <span>{conversation.title}</span>
                <time dateTime={conversation.updated_at}>
                  {formatUpdatedAt(conversation.updated_at)}
                </time>
              </button>
            ))}
          </nav>
          {nextCursor !== null && (
            <button
              className="load-more-conversations"
              type="button"
              disabled={isLoadingMore}
              onClick={onLoadMore}
            >
              {isLoadingMore ? "正在加载…" : "加载更多"}
            </button>
          )}
        </>
      )}

      {listError && (
        <p className="conversation-list-error" role="alert">
          {listError}
        </p>
      )}

      <form className="business-query" onSubmit={onWithdrawalQuery}>
        <p className="sidebar-eyebrow">MOCK BUSINESS</p>
        <label htmlFor="withdrawal-order">提现订单查询</label>
        <div>
          <input
            id="withdrawal-order"
            value={withdrawalOrderId}
            placeholder={user.user_id === "10001" ? "WD-10001" : "WD-10002"}
            onChange={(event) =>
              onWithdrawalOrderIdChange(event.target.value)
            }
          />
          <button
            type="submit"
            disabled={isLoadingWithdrawal || !withdrawalOrderId.trim()}
          >
            查询
          </button>
        </div>
        {withdrawal && (
          <dl>
            <div>
              <dt>状态</dt>
              <dd>{withdrawal.status}</dd>
            </div>
            <div>
              <dt>金额</dt>
              <dd>
                {withdrawal.size} {withdrawal.coin}
              </dd>
            </div>
            <div>
              <dt>网络</dt>
              <dd>{withdrawal.chain}</dd>
            </div>
          </dl>
        )}
      </form>
    </aside>
  );
}
