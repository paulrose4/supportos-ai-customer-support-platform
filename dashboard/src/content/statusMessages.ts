import { formatSyncMode } from "./terminology";

export function knowledgeJobStatusLabel(status?: string, cancelRequested = false) {
  if (cancelRequested) return "正在停止";
  return ({
    preparing: "正在准备",
    queued: "等待开始",
    running: "正在检查",
    blocked: "需要修复",
    succeeded: "已完成",
    failed: "未完成",
    cancelled: "已停止",
  } as Record<string, string>)[status || ""] || "尚未开始";
}

export function knowledgeJobSummary(status: string, mode: string, failedCount = 0) {
  if (status === "preparing") return "正在分批准备完整页面清单，完成后自动开始检查。";
  if (status === "queued") return "任务已提交，系统会在后台开始处理。";
  if (status === "running") return `正在${formatSyncMode(mode)}，当前线上知识不受影响。`;
  if (status === "blocked") return `${failedCount} 个页面需要修复，当前线上知识继续正常使用。`;
  if (status === "succeeded") return mode === "shadow" ? "试运行已完成，未改变线上知识。" : "新知识已通过检查并上线。";
  if (status === "failed") return "本次任务未完成，请查看失败页面并重试。";
  if (status === "cancelled") return "任务已停止，当前线上知识没有变化。";
  return "请先检查网站页面范围。";
}
