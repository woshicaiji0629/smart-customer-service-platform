import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createConversation,
  type Message,
  sendMessage,
} from "./api/conversations";
import "./App.css";

const SUGGESTED_QUESTIONS = [
  "提现完成但钱包没到账怎么办？",
  "如何查找我的 TxID？",
  "企业认证需要准备哪些资料？",
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }

  return "暂时无法连接客服服务，请稍后重试。";
}

export function App() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = input.trim();

    if (!question || isSending) {
      return;
    }

    const pendingId = -Date.now();
    const pendingMessage: Message = {
      id: pendingId,
      conversation_id: conversationId ?? "",
      role: "user",
      content: question,
      sources: [],
      created_at: new Date().toISOString(),
    };

    setInput("");
    setError(null);
    setIsSending(true);
    setMessages((current) => [...current, pendingMessage]);

    try {
      let activeConversationId = conversationId;
      if (!activeConversationId) {
        const conversation = await createConversation();
        activeConversationId = conversation.id;
        setConversationId(activeConversationId);
      }

      const turn = await sendMessage(activeConversationId, question);
      setMessages((current) => [
        ...current.filter((message) => message.id !== pendingId),
        turn.user_message,
        turn.assistant_message,
      ]);
    } catch (requestError) {
      setMessages((current) =>
        current.filter((message) => message.id !== pendingId),
      );
      setInput(question);
      setError(errorMessage(requestError));
    } finally {
      setIsSending(false);
    }
  }

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
      </header>

      <section className="conversation" aria-label="客服对话">
        {messages.length === 0 ? (
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
                  onClick={() => setInput(question)}
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
        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            aria-label="输入问题"
            placeholder="输入你的问题…"
            rows={1}
            maxLength={4000}
            value={input}
            disabled={isSending}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            type="submit"
            disabled={isSending || input.trim().length === 0}
          >
            {isSending ? "发送中" : "发送"}
          </button>
        </form>
        <p className="composer-hint">Enter 发送 · Shift + Enter 换行</p>
      </footer>
    </main>
  );
}
