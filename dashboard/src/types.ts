export interface AdminUser {
  user_id: string;
  tenant_id: string;
  username: string;
  display_name: string;
  roles: string[];
  scopes: string[];
  status: "active" | "disabled";
  created_at: string | null;
  updated_at: string | null;
  authentication_method: string;
  platform_roles: string[];
}

export interface LoginProvider {
  provider: string;
  start_url: string;
}

export interface LoginProviderConfiguration {
  providers: LoginProvider[];
  legacy_login_enabled: boolean;
  email_login_enabled: boolean;
  invite_registration_enabled: boolean;
  self_service_signup_enabled: boolean;
  password_reset_enabled: boolean;
}

export interface WorkspaceOnboardingCode {
  code_id: string;
  policy_id: string;
  code_prefix: string;
  status: string;
  target_email: string;
  expires_at: string;
  consumed_at: string | null;
}

export interface CreatedWorkspaceOnboardingCode {
  code: WorkspaceOnboardingCode;
  enrollment_code: string;
  signup_url: string;
}

export interface EmailAuthenticationResult {
  user: AdminUser | null;
  expires_at: string | null;
  workspace_selection_required: boolean;
}

export interface InvitationPreview {
  tenant_name: string;
  email: string;
  roles: string[];
  expires_at: string;
}

export interface TenantWorkspace {
  tenant_id: string;
  name: string;
  roles: string[];
  active: boolean;
}

export interface PlatformTenant {
  tenant_id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  owner_email: string | null;
  owner_names: string[];
  owner_emails: string[];
  member_count: number;
  disabled_member_count: number;
  site_count: number;
  disabled_site_count: number;
  unverified_site_count: number;
  site_quota_used: number;
  site_limit: number | null;
  plan_id: string | null;
  subscription_status: string | null;
  last_activity_at: string | null;
}

export interface PlatformSite {
  tenant_id: string;
  tenant_name: string;
  site_id: string;
  name: string;
  base_url: string;
  status: string;
  verification_status: string;
  knowledge_publication_state: string;
  manager_names: string[];
  manager_emails: string[];
  created_at: string;
  updated_at: string;
}

export interface PlatformUser {
  user_id: string;
  display_name: string;
  email: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  workspace_count: number;
  workspace_names: string[];
  disabled_workspace_count: number;
  disabled_workspace_names: string[];
  platform_roles: string[];
  last_login_at: string | null;
}

export interface PlatformPage<T> {
  items: T[];
  total: number;
  next_cursor: string | null;
}

export interface PlatformActivity {
  event_type: string;
  actor_subject_id: string | null;
  resource_type: string;
  resource_id: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface PlatformSummary {
  active_workspace_count: number;
  user_count: number;
  pending_onboarding_count: number;
  attention_count: number;
  expiring_code_count: number;
  failed_email_count: number;
  orphan_workspace_count: number;
  recent_activity: PlatformActivity[];
}

export interface PlatformMembership {
  membership_id: string;
  tenant_id: string;
  tenant_name: string;
  user_id: string;
  display_name: string | null;
  email: string | null;
  roles: string[];
  scopes: string[];
  status: string;
}

export interface PlatformOnboardingRecord {
  code_id: string;
  target_email: string;
  status: "issued" | "verification_pending" | "completed" | "expired" | "revoked" | "failed";
  expires_at: string;
  created_by: string;
  created_by_name: string | null;
  created_at: string;
  workspace_name: string | null;
  tenant_id: string | null;
  email_status: string | null;
  email_attempts: number;
  email_sent_at: string | null;
  email_last_error: string | null;
}

export interface TenantInvitation {
  invitation_id: string;
  tenant_id: string;
  tenant_name: string;
  email: string;
  roles: string[];
  status: "pending" | "redeemed" | "revoked" | "expired";
  expires_at: string;
  created_at: string;
  redeemed_at: string | null;
  revoked_at: string | null;
}

export interface CreatedTenantInvitation extends TenantInvitation {
  invitation_url: string;
  email_sent: boolean;
}

export interface AdminSessionItem {
  session_id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  revoked_at: string | null;
  source_fingerprint_prefix: string;
  is_current: boolean;
}

export interface AuditEvent {
  event_id: string;
  event_type: string;
  actor_subject_id: string | null;
  correlation_id: string | null;
  trace_id: string | null;
  resource_type: string;
  resource_id: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface AuditEventPage {
  items: AuditEvent[];
  next_cursor: string | null;
}

export interface ManagedSite {
  site_id: string;
  public_widget_id: string;
  name: string;
  base_url: string;
  allowed_origins: string[];
  widget_daily_message_limit: number;
  primary_language: string;
  install_code: string;
  status: "active" | "disabled";
  credential_key_prefix: string | null;
  credential_status: string | null;
  created_at: string;
  updated_at: string;
  verification_status: "pending" | "verified" | "failed" | "expired";
  verification_method: "dns_txt" | "script" | null;
  verification_token_prefix: string | null;
  verification_expires_at: string | null;
  verified_at: string | null;
}

export interface SiteVerificationChallenge {
  site_id: string;
  method: "dns_txt" | "script";
  dns_name: string;
  dns_value: string;
  script_path: string;
  script_value: string;
  expires_at: string;
  verification_status: string;
}

export type SiteWebDiscoveryMode = "auto" | "hybrid" | "manual";

export interface SiteWebSourceConfig {
  site_id: string;
  discovery_mode: SiteWebDiscoveryMode;
  explicit_sitemap_urls: string[];
  allowed_sitemap_origins: string[];
  config_version: number;
  validation_status: "unvalidated" | "valid" | "invalid";
  validated_at: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface SystemStatus {
  is_ready: boolean;
  failed_dependencies: string[];
  configuration: {
    app_env: string;
    auth_mode: string;
    llm_provider: string;
    embedding_provider: string;
    realtime_backend: string;
    presence_backend: string;
    horizontal_scaling_ready: boolean;
  };
  metrics: Record<string, number>;
  backups: Array<{
    artifact_type: string;
    state: "current" | "stale" | "missing";
    completed_at: string | null;
    age_hours: number | null;
    file_name: string | null;
    size_bytes: number | null;
    sha256: string | null;
    restore_verified_at: string | null;
  }>;
}

export interface Site {
  site_id: string;
  name: string;
  base_url: string;
  status: string;
}

export interface InboxConversation {
  conversation_id: string;
  site_id: string | null;
  customer_id: string | null;
  customer_display_name: string | null;
  visitor_ip_address: string | null;
  visitor_country_code: string | null;
  channel: string;
  status: "open" | "waiting_human" | "resolved";
  ownership_mode: "ai" | "queued" | "human";
  assigned_agent_id: string | null;
  queue_id: string | null;
  priority: "low" | "normal" | "high" | "urgent";
  tags: string[];
  risk_level: number;
  unread_count: number;
  identity_verified: boolean;
  last_message_preview: string | null;
  last_message_at: string | null;
  first_response_at: string | null;
  first_human_response_at: string | null;
  resolved_at: string | null;
  last_read_at: string | null;
  updated_at: string;
  handoff_reason: string | null;
  sla_due_at: string | null;
}

export interface InboxCounts {
  all: number;
  mine: number;
  waiting_human: number;
  sla_risk: number;
  unread: number;
  priority_risk: number;
  high_intent?: number;
  resolved: number;
}

export interface CustomerDirectoryItem {
  customer_id: string;
  display_name: string;
  conversation_count: number;
  last_conversation_at: string | null;
}

export interface ConversationMessage {
  message_id: string;
  role: string;
  content: string;
  message_type: string;
  author_subject_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface Workspace {
  conversation: InboxConversation;
  messages: ConversationMessage[];
  handoff_context: HandoffContext | null;
}

export interface HandoffContext {
  handoff_id: string;
  reason_code: string;
  summary: string;
  risk_level: number;
  user_intent: string | null;
  unresolved_question: string | null;
  ai_attempt: string | null;
  suggested_next_action: string | null;
  reply_draft: string | null;
  priority: string;
  queue_id: string | null;
  failed_tools: string[];
  knowledge_sources: string[];
  commitment_deadline?: string | null;
  order_id?: string | null;
  customer_sentiment?: string | null;
}

export interface SupportQueue {
  queue_id: string;
  name: string;
  description: string;
  is_default: boolean;
  status?: "active" | "disabled";
  site_id?: string | null;
}

export interface SupportQueueMember {
  agent_id: string;
  display_name: string;
  role: string;
  status: string;
}

export interface SupportAgentOption {
  agent_id: string;
  display_name: string;
}

export interface CannedReply {
  reply_id: string;
  title: string;
  content: string;
  shortcut: string;
}

export interface SupportConfiguration {
  queues: SupportQueue[];
  agents: SupportAgentOption[];
  canned_replies: CannedReply[];
}

export interface MemoryItem {
  memory_id: string;
  customer_id: string;
  kind: string;
  content: string;
  source_type: string;
  source_id: string;
  confidence: number;
  consent_status: string;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface HandoffTicket {
  handoff_id: string;
  conversation_id: string;
  customer_id: string | null;
  reason_code: string;
  risk_level: number;
  summary: string;
  status: "pending" | "assigned" | "resolved" | "cancelled";
  priority: string;
  assigned_agent_id: string | null;
  accepted_at: string | null;
  resolved_at: string | null;
  failed_tools: string[];
  knowledge_sources: string[];
  queue_id: string | null;
  user_intent: string | null;
  unresolved_question: string | null;
  ai_attempt: string | null;
  suggested_next_action: string | null;
  reply_draft: string | null;
  created_at: string | null;
}

export interface RealtimeEvent {
  event_id?: string;
  event_type: string;
  resource_type: string;
  resource_id: string;
  payload: Record<string, unknown>;
  occurred_at?: string | null;
}

export type LeadNextAction =
  | "monitor"
  | "monitor_closely"
  | "invite_chat"
  | "continue_conversation"
  | "answer_shipping"
  | "answer_price"
  | "answer_payment"
  | "offer_assistance"
  | "contact_now";

export interface VisitorPresence {
  site_id: string;
  visitor_id: string;
  conversation_id: string | null;
  page_path: string;
  page_kind?: string | null;
  last_seen_at: string;
  first_seen_at?: string;
  page_title?: string | null;
  referrer?: string | null;
  ip_address?: string | null;
  country_code?: string | null;
  browser?: string | null;
  operating_system?: string | null;
  device_type?: string | null;
  language?: string | null;
  timezone?: string | null;
  page_view_count?: number;
  session_started_at?: string;
  current_page_entered_at?: string;
  last_page_view_id?: string | null;
  widget_state?: "closed" | "open";
  presence_source?: "page_load" | "widget";
  runtime_version?: string | null;
  config_version?: string | null;
  connector_type?: "public" | "wordpress" | "static_php" | "cloudflare_worker" | "legacy" | null;
  connector_version?: string | null;
  commercial_intent?: number;
  intent_tier?: "hot" | "warm" | "nurture" | "unknown";
  operation_priority?: "P0" | "P1" | "P2";
  confidence?: number;
  confidence_grade?: "A" | "B" | "C";
  queue_eligible?: boolean;
  next_action?: LeadNextAction;
  signals?: string[];
  freshness?: "current" | "aging" | "stale" | "expired" | "unknown";
  rule_version?: string;
  scored_at?: string | null;
  current_page_dwell_seconds?: number;
  session_active_dwell_seconds?: number;
  session_age_seconds?: number;
  data_coverage?: string[];
}

export type PresenceLoadState = "loading" | "ready" | "stale" | "error";

export interface SupportAnalytics {
  days: number;
  site_id: string | null;
  conversations: number;
  agent_runs: number;
  ai_answers: number;
  handoffs: number;
  human_replied_conversations: number;
  resolved_conversations: number;
  ai_answer_rate: number;
  handoff_rate: number;
  human_reply_rate: number;
  resolution_rate: number;
  average_first_response_seconds: number;
  average_human_response_seconds: number;
  average_resolution_seconds: number;
  unread_conversations: number;
  waiting_human_conversations: number;
  latency_sample_count: number;
  first_response_p50_seconds: number;
  first_response_p95_seconds: number;
  first_response_p99_seconds: number;
  latency_by_route: Array<{
    route: string;
    sample_count: number;
    p50_seconds: number;
    p95_seconds: number;
    p99_seconds: number;
  }>;
  auto_resolution_eligible_conversations: number;
  auto_resolved_conversations: number;
  auto_resolution_rate: number;
  reopened_conversations: number;
  handoff_context_count: number;
  complete_handoff_context_count: number;
  handoff_context_completeness_rate: number;
  legacy_handoff_context_count: number;
  ai_eligible_runs: number;
  forced_order_handoffs: number;
  eligible_ai_answer_rate: number;
}

export interface WidgetConfig {
  welcome_message: string;
  online_message: string;
  offline_message: string;
  business_timezone: string;
  business_hours: Record<string, string>;
  holidays: string[];
  offline_form_enabled: boolean;
  primary_color: string;
  position: "left" | "right";
  agent_name: string;
  agent_avatar_url: string | null;
  mobile_enabled: boolean;
  default_language: string;
  handoff_timeout_seconds: number;
  csat_enabled: boolean;
  customer_address_mode: "formal" | "neutral" | "friendly";
  introduce_on_first_turn: boolean;
  launcher_asset_id: string | null;
  launcher_image_fit: "contain" | "cover";
  agent_avatar_asset_id: string | null;
}

export interface WidgetAsset {
  asset_id: string;
  site_id: string;
  purpose: "launcher" | "avatar";
  status: "active" | "retired";
  url: string;
  width: number;
  height: number;
  source_byte_size: number;
  created_at: string;
}

export interface WidgetConfigVersion {
  version_id: string;
  version_number: number;
  status: "draft" | "published" | "archived";
  config: WidgetConfig;
  created_by: string;
  created_at: string;
  published_at: string | null;
}

export interface WidgetConfigurationState {
  site_id: string;
  published: WidgetConfigVersion | null;
  draft: WidgetConfigVersion | null;
  versions: WidgetConfigVersion[];
}

export interface AutomationConditions {
  site_id: string | null;
  page_path_prefix: string | null;
  business_hours: boolean | null;
  user_intent: string | null;
  minimum_risk_level: number | null;
  authenticated: boolean | null;
  minimum_dwell_seconds: number | null;
  has_assignee: boolean | null;
  has_ticket: boolean | null;
}

export interface AutomationActions {
  queue_id: string | null;
  priority: "low" | "normal" | "high" | "urgent" | null;
  tags: string[];
  create_ticket: boolean;
  direct_handoff: boolean;
}

export interface AutomationRule {
  rule_id: string;
  name: string;
  enabled: boolean;
  sort_order: number;
  conditions: AutomationConditions;
  actions: AutomationActions;
  created_at: string;
  updated_at: string;
}

export interface AutomationExecution {
  execution_id: string;
  rule_id: string;
  conversation_id: string;
  matched: boolean;
  reasons: string[];
  actions_applied: string[];
  occurred_at: string;
}

export interface KnowledgeGap {
  gap_id: string;
  conversation_id: string;
  source: string;
  category: "missing_knowledge" | "incorrect_answer";
  summary: string;
  status: "open" | "resolved";
  created_by: string;
  created_at: string;
  resolved_by: string | null;
  resolution_note: string | null;
  resolved_at: string | null;
}

export interface CustomerExperienceSummary {
  satisfaction_count: number;
  average_satisfaction: number;
  open_knowledge_gaps: number;
  eligible_satisfaction_conversations: number;
  satisfaction_response_rate: number;
  satisfaction_sample_target: number;
  satisfaction_sample_target_met: boolean;
  csat_release_target_met: boolean;
}

export interface WebSyncJobReport {
  pipeline_sync_job_id: string;
  published: boolean;
  discovered_count: number;
  document_count: number;
  changed_document_count: number;
  unchanged_document_count: number;
  http_not_modified_count: number;
  duplicate_count: number;
  duplicate_product_count: number;
  duplicate_product_total?: number;
  duplicate_product_excluded_count?: number;
  duplicate_product_conflict_warning_count?: number;
  duplicate_product_unresolved_count?: number;
  winner_product_count?: number;
  product_count: number;
  pending_removal_count: number;
  expired_count: number;
  indexed_chunk_count: number;
  excluded_count: number;
  failed_count: number;
  errors: Record<string, string>;
  processed_page_count?: number;
  produced_document_count?: number;
  failed_page_count?: number;
  unresolved_product_identity_count?: number;
  blocking_issue_count?: number;
  publication_block_reasons?: string[];
}

export interface WebSyncJob {
  job_id: string;
  site_id: string;
  base_url: string;
  status: "preparing" | "queued" | "running" | "succeeded" | "failed" | "blocked" | "cleanup_pending" | "canceled";
  trigger: "manual" | "scheduled";
  mode: "shadow" | "production";
  publication_status: "not_requested" | "pending" | "published" | "refused";
  manifest_id: string;
  sample_size: number | null;
  phase: "preparing" | "queued" | "processing" | "finalizing" | "awaiting_remediation" | "completed";
  manifest_version: number | null;
  manifest_fingerprint: string | null;
  prepared_count: number;
  expected_count: number;
  completed_count: number;
  succeeded_count: number;
  not_modified_count: number;
  excluded_item_count: number;
  failed_item_count: number;
  canceled_item_count: number;
  requested_by: string;
  requested_at: string;
  started_at: string | null;
  heartbeat_at: string | null;
  completed_at: string | null;
  cancel_requested_at: string | null;
  blocked_at: string | null;
  retention_expires_at: string | null;
  attempt_count: number;
  max_attempts: number;
  max_pages: number;
  duplicate_product_policy?: "first_wins" | "block" | "manual_review" | string;
  duplicate_product_order?: string;
  error_code: string | null;
  error_message: string | null;
  report: WebSyncJobReport | null;
  /** Optional monotonic revision added by the resumable job status contract. */
  state_version?: number | null;
  /** Server-derived execution state; older APIs omit this field. */
  execution_state?:
    | "waiting_for_worker"
    | "preparing"
    | "waiting_retry"
    | "recovery"
    | "recovery_pending"
    | "stalled"
    | "attention_required"
    | "processing"
    | "finalizing"
    | "terminal"
    | string
    | null;
  last_progress_at?: string | null;
  updated_at?: string | null;
  next_wake_at?: string | null;
  available_at?: string | null;
  prepare_stage?: string | null;
  prepare_cursor?: number;
  claim_count?: number;
  failure_attempt_count?: number;
  yield_count?: number;
  stale?: boolean;
  stale_at?: string | null;
  reason_code?: string | null;
  actions?: Array<"cancel" | "retry_failed" | "retry_finalization" | "reconcile_stale_versions" | "abandon" | string> | {
    cancel?: boolean | { allowed: boolean; reason_codes?: string[] };
    retry_failed?: boolean | { allowed: boolean; reason_codes?: string[] };
    retry_finalization?: boolean | { allowed: boolean; reason_codes?: string[] };
    reconcile_stale_versions?: boolean | { allowed: boolean; reason_codes?: string[] };
    abandon?: boolean | { allowed: boolean; reason_codes?: string[] };
  } | null;
}

export interface WebSyncJobItem {
  item_id: string;
  ordinal: number;
  url: string;
  source_sitemap_url: string;
  content_kind: "product" | "guide" | "category" | "utility" | "general" | string;
  status: "pending" | "fetching" | "succeeded" | "not_modified" | "excluded" | "failed" | "canceled";
  attempt_count: number;
  max_attempts: number;
  duration_ms: number | null;
  canonical_url: string | null;
  product_key: string | null;
  normalized_product_key?: string | null;
  normalization_version?: string | null;
  error_code: string | null;
  error_message: string | null;
  outcome_reason: string | null;
  next_attempt_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  winner_item_id?: string | null;
  winner_url?: string | null;
  identity_source?: string | null;
  policy_version?: string | null;
}

export interface WebCrawlDiscoveryAttempt {
  url: string;
  source: string;
  outcome: string;
  final_url: string | null;
}

export interface WebCrawlManifest {
  manifest_id: string;
  site_id: string;
  base_url: string;
  root_sitemap_url: string;
  root_sitemap_urls: string[];
  discovery_method: string;
  warnings: string[];
  coverage_status: string;
  discovery_attempts: WebCrawlDiscoveryAttempt[];
  primary_language: string;
  translation_provider: string;
  status: "ready" | "blocked";
  fingerprint: string;
  version: number;
  policy_version: string;
  source_config_version: number;
  source_config_current: boolean;
  primary_sitemap_urls: string[];
  translated_locales: string[];
  excluded_sitemap_count: number;
  excluded_url_count: number;
  url_count: number;
  content_kind_counts: Record<string, number>;
  blocking_reasons: string[];
  production_sync_enabled: boolean;
  production_blocking_reasons: string[];
  expires_at: string;
  is_expired: boolean;
  created_by: string;
  created_at: string;
}

export interface WebSyncAvailability {
  site_id: string;
  crawler_enabled: boolean;
  site_status: string;
  site_verification_status: string;
  base_url_configured: boolean;
  worker_status: "healthy" | "unavailable" | "unknown" | "not_required";
  max_pages: number;
  manifest_safety_ceiling: number;
  full_shadow_enabled: boolean;
  prepare_batch_size: number;
  worker_concurrency: number;
  domain_concurrency: number;
  preflight_ready: boolean;
  job_processing_ready: boolean;
  blocking_reasons: string[];
  /** Optional freshness/capability metadata from the worker registry. */
  schema_version?: number;
  observed_at?: string;
  valid_until?: string | null;
  worker_heartbeat_at?: string | null;
  worker_heartbeat_age_seconds?: number | null;
  worker_tenant_covered?: boolean;
  worker_instance_id?: string | null;
  worker?: {
    status: "healthy" | "unavailable" | "unknown" | "not_required" | string;
    freshness?: string;
    coverage?: string;
    tenant_covered?: boolean;
    last_heartbeat_at?: string | null;
    heartbeat_expires_at?: string | null;
    heartbeat_age_seconds?: number | null;
    covered_instance_count?: number;
    available_instance_count?: number;
    instance_ids?: string[];
  };
  capabilities?: {
    preflight?: boolean | { allowed: boolean; reason_codes?: string[] };
    enqueue_shadow?: boolean | { allowed: boolean; reason_codes?: string[] };
    enqueue_production?: boolean | { allowed: boolean; reason_codes?: string[] };
  };
}

export interface SiteKnowledgeReadiness {
  site_id: string;
  ready: boolean;
  catalog_ready: boolean;
  policy_ready: boolean;
  care_ready: boolean;
  active_product_count: number;
  active_document_count: number;
  active_snapshot_id: string | null;
  last_successful_sync_at: string | null;
  blocking_reasons: string[];
}
