import type { AuthenticatedUser } from "../api/auth";

interface MockLoginProps {
  users: AuthenticatedUser[];
  error: string | null;
  isChanging: boolean;
  onLogin: (userId: string) => void;
}

export function MockLogin({
  users,
  error,
  isChanging,
  onLogin,
}: MockLoginProps) {
  return (
    <main className="mock-login">
      <p className="eyebrow">DEVELOPMENT ONLY</p>
      <h1>选择 Mock 用户</h1>
      <p>身份仅用于本地验证 Session 和业务数据隔离。</p>
      {error && <p className="error-message">{error}</p>}
      <div className="mock-user-list">
        {users.map((user) => (
          <button
            key={user.user_id}
            type="button"
            disabled={isChanging}
            onClick={() => onLogin(user.user_id)}
          >
            <strong>{user.display_name}</strong>
            <span>UID {user.user_id}</span>
          </button>
        ))}
      </div>
    </main>
  );
}
