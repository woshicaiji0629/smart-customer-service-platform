import {
  type KeyboardEvent,
  type SubmitEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  createConversation,
  getConversation,
  type Message,
  sendMessage,
} from "./api/conversations";
import {
  type AuthenticatedUser,
  getCurrentUser,
  listMockUsers,
  logout,
  mockLogin,
} from "./api/auth";
import { getWithdrawal, type Withdrawal } from "./api/business";
import { ApiError } from "./api/client";
import "./App.css";

const CONVERSATION_STORAGE_KEY_PREFIX = "smart-support-conversation-id";
const CONVERSATION_LIST_STORAGE_KEY_PREFIX = "smart-support-conversations";
const CONVERSATION_TITLE_LENGTH = 24;

interface StoredConversation {
  id: string;
  title: string;
  updatedAt: string;
}

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

function isStoredConversation(value: unknown): value is StoredConversation {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.id === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.updatedAt === "string"
  );
}

function userStorageKey(prefix: string, userId: string): string {
  return `${prefix}:${userId}`;
}

function readStoredConversations(userId: string): StoredConversation[] {
  const stored = window.localStorage.getItem(
    userStorageKey(CONVERSATION_LIST_STORAGE_KEY_PREFIX, userId),
  );
  if (!stored) {
    return [];
  }

  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || !parsed.every(isStoredConversation)) {
    throw new Error("会话列表格式无效");
  }

  return parsed;
}

function titleFromMessages(messages: Message[]): string {
  const firstQuestion = messages.find((message) => message.role === "user");
  return firstQuestion ? titleFromQuestion(firstQuestion.content) : "空白会话";
}

function titleFromQuestion(question: string): string {
  const normalized = question.trim().replace(/\s+/g, " ");
  return normalized.length > CONVERSATION_TITLE_LENGTH
    ? `${normalized.slice(0, CONVERSATION_TITLE_LENGTH)}…`
    : normalized;
}

function upsertConversation(
  conversations: StoredConversation[],
  conversation: StoredConversation,
): StoredConversation[] {
  return [
    conversation,
    ...conversations.filter((item) => item.id !== conversation.id),
  ].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
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

export function App() {
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [mockUsers, setMockUsers] = useState<AuthenticatedUser[]>([]);
  const [isLoadingAuth, setIsLoadingAuth] = useState(true);
  const [isChangingAuth, setIsChangingAuth] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [withdrawalOrderId, setWithdrawalOrderId] = useState("");
  const [withdrawal, setWithdrawal] = useState<Withdrawal | null>(null);
  const [isLoadingWithdrawal, setIsLoadingWithdrawal] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<StoredConversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [hasLoadedStorage, setHasLoadedStorage] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let isActive = true;

    async function restoreUser() {
      try {
        const currentUser = await getCurrentUser();
        if (isActive) {
          setUser(currentUser);
        }
      } catch (requestError) {
        if (!(requestError instanceof ApiError && requestError.status === 401)) {
          if (isActive) {
            setAuthError(errorMessage(requestError));
          }
        }
        try {
          const users = await listMockUsers();
          if (isActive) {
            setMockUsers(users);
          }
        } catch (usersError) {
          if (isActive) {
            setAuthError(errorMessage(usersError));
          }
        }
      } finally {
        if (isActive) {
          setIsLoadingAuth(false);
        }
      }
    }

    void restoreUser();
    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    if (!user) {
      setIsLoadingHistory(false);
      return;
    }

    const activeUser = user;
    let isActive = true;
    setHasLoadedStorage(false);
    setIsLoadingHistory(true);
    setConversationId(null);
    setConversations([]);
    setMessages([]);

    async function restoreConversation() {
      let storedConversationId: string | null;
      let storedConversations: StoredConversation[];

      try {
        storedConversations = readStoredConversations(activeUser.user_id);
        storedConversationId = window.localStorage.getItem(
          userStorageKey(CONVERSATION_STORAGE_KEY_PREFIX, activeUser.user_id),
        );
      } catch {
        if (isActive) {
          setError("无法读取浏览器中的历史会话。");
          setHasLoadedStorage(true);
          setIsLoadingHistory(false);
        }
        return;
      }

      if (
        storedConversationId &&
        !storedConversations.some((item) => item.id === storedConversationId)
      ) {
        storedConversations = upsertConversation(storedConversations, {
          id: storedConversationId,
          title: "历史会话",
          updatedAt: new Date(0).toISOString(),
        });
      }

      setConversations(storedConversations);

      if (!storedConversationId) {
        setHasLoadedStorage(true);
        setIsLoadingHistory(false);
        return;
      }

      setConversationId(storedConversationId);

      try {
        const history = await getConversation(storedConversationId);
        if (isActive) {
          setMessages(history.messages);
          setConversations((current) =>
            upsertConversation(current, {
              id: history.id,
              title: titleFromMessages(history.messages),
              updatedAt: history.updated_at,
            }),
          );
        }
      } catch (requestError) {
        if (!isActive) {
          return;
        }

        if (requestError instanceof ApiError && requestError.status === 404) {
          setConversationId(null);
          setConversations((current) =>
            current.filter((item) => item.id !== storedConversationId),
          );
        } else {
          setError(errorMessage(requestError));
        }
      } finally {
        if (isActive) {
          setHasLoadedStorage(true);
          setIsLoadingHistory(false);
        }
      }
    }

    void restoreConversation();

    return () => {
      isActive = false;
    };
  }, [user]);

  useEffect(() => {
    if (!hasLoadedStorage || !user) {
      return;
    }

    try {
      window.localStorage.setItem(
        userStorageKey(CONVERSATION_LIST_STORAGE_KEY_PREFIX, user.user_id),
        JSON.stringify(conversations),
      );
    } catch {
      setError("无法在浏览器中保存会话列表。");
    }
  }, [conversations, hasLoadedStorage, user]);

  useEffect(() => {
    if (!hasLoadedStorage || !user) {
      return;
    }

    try {
      if (conversationId) {
        window.localStorage.setItem(
          userStorageKey(CONVERSATION_STORAGE_KEY_PREFIX, user.user_id),
          conversationId,
        );
      } else {
        window.localStorage.removeItem(
          userStorageKey(CONVERSATION_STORAGE_KEY_PREFIX, user.user_id),
        );
      }
    } catch {
      setError("无法在浏览器中保存当前会话。");
    }
  }, [conversationId, hasLoadedStorage, user]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

  function handleNewConversation() {
    if (isLoadingHistory || isSending) {
      return;
    }

    setConversationId(null);
    setMessages([]);
    setInput("");
    setError(null);
  }

  async function handleSelectConversation(selectedConversationId: string) {
    if (
      selectedConversationId === conversationId ||
      isLoadingHistory ||
      isSending
    ) {
      return;
    }

    setError(null);
    setIsLoadingHistory(true);

    try {
      const history = await getConversation(selectedConversationId);
      setConversationId(history.id);
      setMessages(history.messages);
      setConversations((current) =>
        upsertConversation(current, {
          id: history.id,
          title: titleFromMessages(history.messages),
          updatedAt: history.updated_at,
        }),
      );
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 404) {
        setConversations((current) =>
          current.filter((item) => item.id !== selectedConversationId),
        );
        setError("该历史会话已不存在。");
      } else {
        setError(errorMessage(requestError));
      }
    } finally {
      setIsLoadingHistory(false);
    }
  }

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
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
        setConversations((current) =>
          upsertConversation(current, {
            id: conversation.id,
            title: titleFromQuestion(question),
            updatedAt: conversation.updated_at,
          }),
        );
      }

      const turn = await sendMessage(activeConversationId, question);
      setMessages((current) => [
        ...current.filter((message) => message.id !== pendingId),
        turn.user_message,
        turn.assistant_message,
      ]);
      setConversations((current) => {
        const existing = current.find(
          (conversation) => conversation.id === activeConversationId,
        );
        return upsertConversation(current, {
          id: activeConversationId,
          title: existing?.title ?? titleFromQuestion(question),
          updatedAt: turn.assistant_message.created_at,
        });
      });
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

  async function handleLogin(userId: string) {
    setIsChangingAuth(true);
    setAuthError(null);
    try {
      setUser(await mockLogin(userId));
    } catch (requestError) {
      setAuthError(errorMessage(requestError));
    } finally {
      setIsChangingAuth(false);
    }
  }

  async function handleLogout() {
    setIsChangingAuth(true);
    setAuthError(null);
    try {
      await logout();
      setUser(null);
      setConversationId(null);
      setConversations([]);
      setMessages([]);
      setHasLoadedStorage(false);
      setWithdrawal(null);
      if (mockUsers.length === 0) {
        setMockUsers(await listMockUsers());
      }
    } catch (requestError) {
      setAuthError(errorMessage(requestError));
    } finally {
      setIsChangingAuth(false);
    }
  }

  async function handleWithdrawalQuery(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const orderId = withdrawalOrderId.trim();
    if (!orderId || isLoadingWithdrawal) {
      return;
    }
    setIsLoadingWithdrawal(true);
    setWithdrawal(null);
    setError(null);
    try {
      setWithdrawal(await getWithdrawal(orderId));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setIsLoadingWithdrawal(false);
    }
  }

  if (isLoadingAuth) {
    return <div className="auth-loading">正在检查登录状态…</div>;
  }

  if (!user) {
    return (
      <main className="mock-login">
        <p className="eyebrow">DEVELOPMENT ONLY</p>
        <h1>选择 Mock 用户</h1>
        <p>身份仅用于本地验证 Session 和业务数据隔离。</p>
        {authError && <p className="error-message">{authError}</p>}
        <div className="mock-user-list">
          {mockUsers.map((mockUser) => (
            <button
              key={mockUser.user_id}
              type="button"
              disabled={isChangingAuth}
              onClick={() => void handleLogin(mockUser.user_id)}
            >
              <strong>{mockUser.display_name}</strong>
              <span>UID {mockUser.user_id}</span>
            </button>
          ))}
        </div>
      </main>
    );
  }

  return (
    <div className="app-layout">
      <aside className="conversation-sidebar" aria-label="会话管理">
        <div className="sidebar-header">
          <div>
            <p className="sidebar-eyebrow">CONVERSATIONS</p>
            <h2>会话</h2>
          </div>
          <button
            type="button"
            onClick={handleNewConversation}
            disabled={isLoadingHistory || isSending}
          >
            新建会话
          </button>
        </div>
        {conversations.length === 0 ? (
          <p className="conversation-empty">暂无历史会话</p>
        ) : (
          <nav className="conversation-list" aria-label="历史会话">
            {conversations.map((conversation) => (
              <button
                className={
                  conversation.id === conversationId ? "is-active" : undefined
                }
                type="button"
                key={conversation.id}
                onClick={() => void handleSelectConversation(conversation.id)}
                disabled={isLoadingHistory || isSending}
              >
                <span>{conversation.title}</span>
                <time dateTime={conversation.updatedAt}>
                  {formatUpdatedAt(conversation.updatedAt)}
                </time>
              </button>
            ))}
          </nav>
        )}
        <form className="business-query" onSubmit={handleWithdrawalQuery}>
          <p className="sidebar-eyebrow">MOCK BUSINESS</p>
          <label htmlFor="withdrawal-order">提现订单查询</label>
          <div>
            <input
              id="withdrawal-order"
              value={withdrawalOrderId}
              placeholder={user.user_id === "10001" ? "WD-10001" : "WD-10002"}
              onChange={(event) => setWithdrawalOrderId(event.target.value)}
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
              <div><dt>状态</dt><dd>{withdrawal.status}</dd></div>
              <div><dt>金额</dt><dd>{withdrawal.size} {withdrawal.coin}</dd></div>
              <div><dt>网络</dt><dd>{withdrawal.chain}</dd></div>
            </dl>
          )}
        </form>
      </aside>

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
          <button
            type="button"
            disabled={isChangingAuth}
            onClick={() => void handleLogout()}
          >
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
            disabled={isLoadingHistory || isSending}
            onChange={(event) => setInput(event.target.value)}
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
    </div>
  );
}
