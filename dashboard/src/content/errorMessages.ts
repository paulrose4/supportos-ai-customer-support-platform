export interface UserFacingError {
  title: string;
  message: string;
  technicalDetail: string;
}

export function getRecoverableError(error: unknown, fallback: string): UserFacingError {
  const detail = error instanceof Error ? error.message : String(error || "未知错误");
  const unavailable = /(?:500|502|503|504|network|fetch|连接|不可用)/i.test(detail);
  return {
    title: unavailable ? "服务暂时不可用" : fallback,
    message: unavailable
      ? "当前操作没有完成，请稍后重试。已有数据不会因此丢失。"
      : `${fallback}。请检查填写内容后重试。`,
    technicalDetail: detail,
  };
}

export function formatErrorMessage(error: unknown, fallback: string) {
  return getRecoverableError(error, fallback).message;
}
