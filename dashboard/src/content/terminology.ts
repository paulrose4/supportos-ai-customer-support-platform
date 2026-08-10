export type RiskTone = "neutral" | "amber" | "red";

export interface RiskPresentation {
  label: string;
  description: string;
  action: string;
  tone: RiskTone;
}

const riskLevels: Record<number, RiskPresentation> = {
  0: {
    label: "低风险",
    description: "当前没有需要额外人工确认的风险信号。",
    action: "按常规流程处理",
    tone: "neutral",
  },
  1: {
    label: "一般风险",
    description: "回复前建议确认客户身份或问题上下文。",
    action: "确认信息后回复",
    tone: "amber",
  },
  2: {
    label: "较高风险",
    description: "可能涉及订单、身份或未经确认的信息。",
    action: "需要人工核对",
    tone: "amber",
  },
  3: {
    label: "高风险",
    description: "当前问题不能由 AI 直接作出业务承诺。",
    action: "必须由人工处理",
    tone: "red",
  },
};

export function formatRiskLevel(level: number): RiskPresentation {
  if (level >= 3) return riskLevels[3];
  if (level >= 2) return riskLevels[2];
  if (level >= 1) return riskLevels[1];
  return riskLevels[0];
}

export function formatOwnership(value: string) {
  if (value === "human") return "人工处理中";
  if (value === "queued") return "等待人工接管";
  return "AI 正在处理";
}

export function formatPriority(value: string) {
  return ({ low: "低", normal: "普通", high: "高", urgent: "紧急" } as Record<string, string>)[value] || value;
}

export function formatSyncMode(value: string) {
  return value === "shadow" ? "试运行" : "更新线上知识";
}

const roleLabels: Record<string, string> = {
  tenant_owner: "工作区管理员",
  support_manager: "客服经理",
  support_agent: "客服人员",
  knowledge_admin: "知识管理员",
  auditor: "审计员",
  platform_owner: "平台所有者",
  platform_operator: "平台运营",
  platform_auditor: "平台审计",
};

const localAccountNames: Record<string, string> = {
  "Local Tenant Owner": "本地工作区管理员",
  "Local Support Manager": "本地客服经理",
  "Local Support Agent": "本地客服人员",
  "Local Knowledge Admin": "本地知识管理员",
  "Local Auditor": "本地审计员",
};

export function formatRoleLabel(value: string) {
  return roleLabels[value] || value;
}

export function formatAccountDisplayName(value: string, roles: string[] = []) {
  const normalized = value.trim();
  if (localAccountNames[normalized]) return localAccountNames[normalized];
  if (normalized === "Local User" && roles.length > 0) return `本地${formatRoleLabel(roles[0])}`;
  return value;
}

export const terminology = {
  responseDeadline: "回复时限",
  responseOverdue: "回复已超时",
  supportGroup: "客服分组",
  assignmentSettings: "分配设置",
  workspace: "工作区",
  websiteChat: "网站客服窗口",
  siteIntegration: "网站接入组件",
  customerSatisfaction: "客户满意度",
  testRun: "试运行",
  publishKnowledge: "更新线上知识",
  pageInventory: "网站页面清单",
  verifiedKnowledge: "已验证知识片段",
  publishedVersion: "已发布版本",
  publishChecks: "发布条件检查",
  backgroundService: "后台处理服务",
  requestTraceId: "请求追踪编号",
  rolePermissions: "角色权限控制",
  siteIdentifier: "网站标识",
} as const;
