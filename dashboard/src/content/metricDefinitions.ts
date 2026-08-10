export const metricHelp = {
  aiAnswerRate: "在适合由 AI 处理的咨询中，AI 成功提供有效回答的比例。",
  firstResponse95: "95% 的会话会在该时间内收到第一次回复。",
  humanResponse: "客户进入人工处理后，等待第一条人工回复的平均时间。",
  resolutionTime: "从客户发起咨询到会话标记为已解决的平均时间。",
  satisfaction: "客户在问题解决后提交的平均评分。",
} as const;

export function formatDuration(seconds: number) {
  if (!seconds) return "暂无数据";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}

export function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}
