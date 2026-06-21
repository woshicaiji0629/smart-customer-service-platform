import { ChatWorkspace } from "./components/ChatWorkspace";
import { ConversationSidebar } from "./components/ConversationSidebar";
import { MockLogin } from "./components/MockLogin";
import { useAuth } from "./hooks/useAuth";
import { useConversations } from "./hooks/useConversations";
import { useWithdrawal } from "./hooks/useWithdrawal";
import "./App.css";

export function App() {
  const auth = useAuth();
  const conversations = useConversations(auth.user?.user_id ?? null);
  const withdrawal = useWithdrawal(auth.user?.user_id ?? null);

  if (auth.isLoading) {
    return <div className="auth-loading">正在检查登录状态…</div>;
  }

  if (!auth.user) {
    return (
      <MockLogin
        users={auth.mockUsers}
        error={auth.error}
        isChanging={auth.isChanging}
        onLogin={(userId) => void auth.login(userId)}
      />
    );
  }

  return (
    <div className="app-layout">
      <ConversationSidebar
        user={auth.user}
        conversations={conversations.conversations}
        activeConversationId={conversations.conversationId}
        nextCursor={conversations.nextCursor}
        isLoadingHistory={conversations.isLoadingHistory}
        isSending={conversations.isSending}
        isLoadingMore={conversations.isLoadingMore}
        listError={conversations.listError}
        withdrawalOrderId={withdrawal.orderId}
        withdrawal={withdrawal.withdrawal}
        isLoadingWithdrawal={withdrawal.isLoading}
        onNewConversation={() => {
          withdrawal.clearError();
          conversations.startNewConversation();
        }}
        onLoadMore={() => void conversations.loadMore()}
        onSelectConversation={(conversationId) => {
          withdrawal.clearError();
          void conversations.selectConversation(conversationId);
        }}
        onWithdrawalOrderIdChange={withdrawal.setOrderId}
        onWithdrawalQuery={(event) => {
          conversations.clearError();
          void withdrawal.query(event);
        }}
      />
      <ChatWorkspace
        user={auth.user}
        messages={conversations.messages}
        input={conversations.input}
        error={conversations.error ?? withdrawal.error}
        isChangingAuth={auth.isChanging}
        isLoadingHistory={conversations.isLoadingHistory}
        isSending={conversations.isSending}
        onInputChange={conversations.setInput}
        onSubmit={(event) => {
          withdrawal.clearError();
          void conversations.submit(event);
        }}
        onLogout={() => void auth.logout()}
      />
    </div>
  );
}
