import { type SubmitEvent, useLayoutEffect, useRef, useState } from "react";

import { type Withdrawal, getWithdrawal } from "../api/business";
import { getErrorMessage } from "../api/client";

export function useWithdrawal(userId: string | null) {
  const requestVersionRef = useRef(0);
  const [orderId, setOrderId] = useState("");
  const [withdrawal, setWithdrawal] = useState<Withdrawal | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 用户变化时先使旧查询失效，再在浏览器绘制前清空用户相关数据。
  useLayoutEffect(() => {
    requestVersionRef.current += 1;
    setOrderId("");
    setWithdrawal(null);
    setIsLoading(false);
    setError(null);

    return () => {
      requestVersionRef.current += 1;
    };
  }, [userId]);

  function createRequestScope(): () => boolean {
    const requestVersion = requestVersionRef.current;
    return () => requestVersionRef.current === requestVersion;
  }

  async function query(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedOrderId = orderId.trim();
    if (!normalizedOrderId || isLoading) {
      return;
    }

    const isRequestActive = createRequestScope();
    setIsLoading(true);
    setWithdrawal(null);
    setError(null);
    try {
      const result = await getWithdrawal(normalizedOrderId);
      if (isRequestActive()) {
        setWithdrawal(result);
      }
    } catch (requestError) {
      if (isRequestActive()) {
        setError(getErrorMessage(requestError));
      }
    } finally {
      if (isRequestActive()) {
        setIsLoading(false);
      }
    }
  }

  return {
    orderId,
    withdrawal,
    isLoading,
    error,
    setOrderId,
    clearError: () => setError(null),
    query,
  };
}
