import { useEffect, useState } from "react";

import {
  type AuthenticatedUser,
  getCurrentUser,
  listMockUsers,
  logout as logoutRequest,
  mockLogin,
} from "../api/auth";
import { ApiError, getErrorMessage } from "../api/client";

export function useAuth() {
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [mockUsers, setMockUsers] = useState<AuthenticatedUser[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isChanging, setIsChanging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 启动时只检查一次 Session。未登录返回 401 属于正常情况，
  // 此时继续加载开发环境使用的 Mock 用户列表。
  useEffect(() => {
    let isActive = true;

    async function restoreUser() {
      try {
        const currentUser = await getCurrentUser();
        if (isActive) {
          setUser(currentUser);
        }
      } catch (requestError) {
        if (
          isActive &&
          !(requestError instanceof ApiError && requestError.status === 401)
        ) {
          setError(getErrorMessage(requestError));
        }

        try {
          const users = await listMockUsers();
          if (isActive) {
            setMockUsers(users);
          }
        } catch (usersError) {
          if (isActive) {
            setError(getErrorMessage(usersError));
          }
        }
      } finally {
        if (isActive) {
          setIsLoading(false);
        }
      }
    }

    void restoreUser();
    return () => {
      // 避免组件卸载后，延迟返回的请求继续修改状态。
      isActive = false;
    };
  }, []);

  async function login(userId: string) {
    setIsChanging(true);
    setError(null);
    try {
      setUser(await mockLogin(userId));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsChanging(false);
    }
  }

  async function logout(): Promise<boolean> {
    setIsChanging(true);
    setError(null);
    try {
      await logoutRequest();
      setUser(null);
      if (mockUsers.length === 0) {
        try {
          setMockUsers(await listMockUsers());
        } catch (usersError) {
          // Session 已经退出，Mock 用户加载失败不应改变退出结果。
          setError(getErrorMessage(usersError));
        }
      }
      return true;
    } catch (requestError) {
      setError(getErrorMessage(requestError));
      return false;
    } finally {
      setIsChanging(false);
    }
  }

  return {
    user,
    mockUsers,
    isLoading,
    isChanging,
    error,
    login,
    logout,
  };
}
