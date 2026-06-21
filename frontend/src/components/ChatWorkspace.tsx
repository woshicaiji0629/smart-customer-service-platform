import { type KeyboardEvent, type SubmitEvent, useEffect, useRef } from "react";

import type { AuthenticatedUser } from "../api/auth";
import type { Message } from "../api/conversations";

const SUGGESTED_QUESTIONS = [
  "提现完成但钱包没到账怎么办？",
  "如何查找我的 TxID？",
  "企业认证需要准备哪些资料？",
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
  onLogout,
}: ChatWorkspaceProps) {
  const endRef = useRef<HTMLDivElement>(null);

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
            <h2>有什么可以帮你？</h2>
            <p>我会根据官方帮助资料，为你查找答案并附上来源。</p>
            <div className="suggestions" aria-label="推荐问题">
              {SUGGESTED_QUESTIONS.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => onInputChange(question)}
                >
                  {question}
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
        <form className="composer" onSubmit={onSubmit}>
          <textarea
            aria-label="输入问题"
            placeholder="输入你的问题…"
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
