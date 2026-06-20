import { request } from "./client";

export interface AuthenticatedUser {
  user_id: string;
  display_name: string;
}

export function listMockUsers(): Promise<AuthenticatedUser[]> {
  return request<AuthenticatedUser[]>("/auth/mock-users");
}

export function getCurrentUser(): Promise<AuthenticatedUser> {
  return request<AuthenticatedUser>("/auth/me");
}

export function mockLogin(userId: string): Promise<AuthenticatedUser> {
  return request<AuthenticatedUser>("/auth/mock-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}
