export const helpText = {
  responseDeadline: "超过回复时限的会话应优先处理，避免客户继续等待。",
  risk: "风险等级由固定规则判断。较高或高风险问题需要人工核对后回复。",
  supportGroup: "客服分组决定由哪一组人员负责处理该会话。",
  customerSatisfaction: "客户在问题解决后提交的满意度评分。",
  testRun: "检查网站知识是否能正确读取和检索，不会改变当前线上回答。",
  publishKnowledge: "全部检查通过后，用新版本替换当前线上知识。",
  requestTraceId: "技术人员可使用该编号定位一次操作的完整处理记录。",
  advancedDiagnostics: "以下信息用于技术排查，日常运营通常无需关注。",
  globalSiteScope: "当前页面的数据范围由顶部的网站选择器统一控制。",
  knowledgeReady: "网站客服可以使用已发布的商品、政策和服务知识回答访客。",
  knowledgeNotReady: "部分回答能力可能受限，请根据阻断原因完成修复。",
} as const;
