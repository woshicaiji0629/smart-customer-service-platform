import { request } from "./client";

export interface Withdrawal {
  order_id: string;
  coin: string;
  size: string;
  status: "pending" | "fail" | "success";
  chain: string;
  updated_at: string;
}

export function getWithdrawal(orderId: string): Promise<Withdrawal> {
  return request<Withdrawal>(
    `/business/withdrawals/${encodeURIComponent(orderId)}`,
  );
}
