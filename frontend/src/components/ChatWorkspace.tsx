import { type KeyboardEvent, type SubmitEvent, useEffect, useRef } from "react";

import type { AuthenticatedUser } from "../api/auth";
import type { Message } from "../api/conversations";
import { composerGuidanceFromMessages } from "../hooks/conversationHelpers";

const SUGGESTED_QUESTIONS = [
  {
    label: "提现未到账",
    question: "提现完成但钱包没到账怎么办？",
  },
  {
    label: "充值未到账",
    question: "充值一直没到账，我应该怎么处理？",
  },
  {
    label: "身份认证失败",
    question: "身份认证失败一般是什么原因？",
  },
  {
    label: "账户安全",
    question: "发现陌生登录，我应该怎么保护账户？",
  },
  {
    label: "查提现订单",
    question: "查询 WD-10001",
  },
  {
    label: "查充值 TxID",
    question: "查询 TX-10001",
  },
];

interface ChatWorkspaceProps {
  user: AuthenticatedUser;
  messages: Message[];
  input: string;
  error: string | null;
  isChangingAuth: boolean;
  isLoadingHistory: boolean;
  isSending: boolean;
  onInputChange: (value: string) => void;
  onSubmit: (event: SubmitEvent<HTMLFormElement>) => void;
  onSuggestedQuestion: (question: string) => void;
  onLogout: () => void;
}

export function ChatWorkspace({
  user,
  messages,
  input,
  error,
  isChangingAuth,
  isLoadingHistory,
  isSending,
  onInputChange,
  onSubmit,
  onSuggestedQuestion,
  onLogout,
}: ChatWorkspaceProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const composerGuidance = composerGuidanceFromMessages(messages);

  // 消息或发送状态变化后滚动到底部，保持最新消息和加载提示可见。
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand-mark" aria-hidden="true">
          S
        </div>
        <div>
          <p className="eyebrow">SMART SUPPORT</p>
          <h1>智能客服</h1>
        </div>
        <span className="service-status">
          <span className="status-dot" aria-hidden="true" /> 在线
        </span>
        <div className="user-menu">
          <span>{user.display_name}</span>
          <button type="button" disabled={isChangingAuth} onClick={onLogout}>
            退出
          </button>
        </div>
      </header>

      <section className="conversation" aria-label="客服对话">
        {isLoadingHistory ? (
          <div className="history-loading" role="status">
            正在恢复历史会话…
          </div>
        ) : messages.length === 0 ? (
          <div className="welcome-panel">
            <p className="welcome-icon" aria-hidden="true">
              24/7
            </p>
            <h2>先用智能客服解决问题</h2>
            <p>选择高频场景或直接输入订单号、TxID，我会优先自动查询和匹配官方资料。</p>
            <div className="suggestions" aria-label="推荐问题">
              {SUGGESTED_QUESTIONS.map((item) => (
                <button
                  key={item.question}
                  type="button"
                  disabled={isLoadingHistory || isSending}
                  onClick={() => onSuggestedQuestion(item.question)}
                >
                  <span>{item.label}</span>
                  <strong>{item.question}</strong>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="message-list" aria-live="polite">
            {messages.map((message) => (
              <article
                className={`message message--${message.role}`}
                key={message.id}
              >
                <p className="message-label">
                  {message.role === "user" ? "你" : "智能客服"}
                </p>
                <div className="message-content">{message.content}</div>
                {message.sources.length > 0 && (
                  <div className="sources">
                    <p>参考资料</p>
                    <ol>
                      {message.sources.map((source) => (
                        <li key={source.article_id}>
                          <a
                            href={source.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {source.title}
                          </a>
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
              </article>
            ))}
            {isSending && (
              <div className="thinking" role="status">
                <span />
                <span />
                <span />
                正在查找资料
              </div>
            )}
            <div ref={endRef} />
          </div>
        )}
      </section>

      <footer className="composer-area">
        {error && <p className="error-message">{error}</p>}
        <div className="input-guides" aria-label="提问提示">
          {composerGuidance.guides.map((guide) => (
            <span key={guide}>{guide}</span>
          ))}
        </div>
        <form className="composer" onSubmit={onSubmit}>
          <textarea
            aria-label="输入问题"
            placeholder={composerGuidance.placeholder}
            rows={1}
            maxLength={4000}
            value={input}
            disabled={isLoadingHistory || isSending}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            type="submit"
            disabled={
              isLoadingHistory || isSending || input.trim().length === 0
            }
          >
            {isSending ? "发送中" : "发送"}
          </button>
        </form>
        <p className="composer-hint">Enter 发送 · Shift + Enter 换行</p>
      </footer>
    </main>
  );
}
