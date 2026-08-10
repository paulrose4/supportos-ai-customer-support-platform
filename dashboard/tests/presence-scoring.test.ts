import { describe, expect, it } from "vitest";

import {
  actionablePurchaseOpportunities,
  isPurchaseOpportunityActionable,
  nextActionLabel,
  purchaseOpportunityConversationIds,
  purchaseOpportunitySignalLabels,
  signalLabel,
} from "../src/pages";
import type { LeadNextAction, VisitorPresence } from "../src/types";

const NOW = Date.parse("2026-08-06T00:05:00Z");

function presence(values: Partial<VisitorPresence>): VisitorPresence {
  return {
    site_id: "site-a",
    visitor_id: "visitor-a",
    conversation_id: "conversation-a",
    page_path: "/",
    last_seen_at: "2026-08-06T00:04:30Z",
    scored_at: "2026-08-06T00:04:30Z",
    freshness: "current",
    ...values,
  };
}

describe("canonical visitor intent projection", () => {
  it("uses the server score instead of rebuilding the legacy browser formula", () => {
    const legacyFalsePositive = presence({
      first_seen_at: "2026-08-05T23:50:00Z",
      page_view_count: 8,
      commercial_intent: 24,
    });

    expect(purchaseOpportunityConversationIds([legacyFalsePositive], NOW)).toEqual(new Set());
  });

  it("includes a linked conversation at the canonical queue threshold", () => {
    const actionable = presence({
      commercial_intent: 35,
      intent_tier: "warm",
      queue_eligible: true,
    });

    expect(purchaseOpportunityConversationIds([actionable], NOW)).toEqual(
      new Set(["conversation-a"]),
    );
  });

  it("sorts by operation priority, freshness, score, and then last activity", () => {
    const items = [
      presence({
        visitor_id: "p1-aging-high",
        operation_priority: "P1",
        freshness: "aging",
        commercial_intent: 100,
        queue_eligible: true,
        last_seen_at: "2026-08-06T00:02:00Z",
      }),
      presence({
        visitor_id: "p1-current-50-older",
        operation_priority: "P1",
        commercial_intent: 50,
        queue_eligible: true,
        last_seen_at: "2026-08-06T00:04:20Z",
      }),
      presence({
        visitor_id: "p0-aging-low",
        operation_priority: "P0",
        freshness: "aging",
        commercial_intent: 35,
        queue_eligible: true,
        last_seen_at: "2026-08-06T00:02:30Z",
      }),
      presence({
        visitor_id: "p1-current-99",
        operation_priority: "P1",
        commercial_intent: 99,
        queue_eligible: true,
        last_seen_at: "2026-08-06T00:04:10Z",
      }),
      presence({
        visitor_id: "p1-current-50-newer",
        operation_priority: "P1",
        commercial_intent: 50,
        queue_eligible: true,
        last_seen_at: "2026-08-06T00:04:50Z",
      }),
    ];

    expect(actionablePurchaseOpportunities(items, NOW).map((item) => item.visitor_id)).toEqual([
      "p0-aging-low",
      "p1-current-99",
      "p1-current-50-newer",
      "p1-current-50-older",
      "p1-aging-high",
    ]);
  });

  it("fails closed when either the observation or score snapshot is no longer fresh", () => {
    const valid = presence({ queue_eligible: true });
    const oldObservation = presence({
      visitor_id: "old-observation",
      conversation_id: "old-observation-conversation",
      queue_eligible: true,
      last_seen_at: "2026-08-05T23:59:59Z",
    });
    const oldScore = presence({
      visitor_id: "old-score",
      conversation_id: "old-score-conversation",
      queue_eligible: true,
      scored_at: "2026-08-05T23:59:59Z",
    });
    const staleServerDecision = presence({
      visitor_id: "stale-decision",
      conversation_id: "stale-decision-conversation",
      queue_eligible: true,
      freshness: "stale",
    });
    const missingScoreTime = presence({
      visitor_id: "missing-score-time",
      conversation_id: "missing-score-time-conversation",
      queue_eligible: true,
      scored_at: null,
    });

    expect(isPurchaseOpportunityActionable(valid, NOW)).toBe(true);
    expect(isPurchaseOpportunityActionable(oldObservation, NOW)).toBe(false);
    expect(isPurchaseOpportunityActionable(oldScore, NOW)).toBe(false);
    expect(isPurchaseOpportunityActionable(staleServerDecision, NOW)).toBe(false);
    expect(isPurchaseOpportunityActionable(missingScoreTime, NOW)).toBe(false);
    expect(purchaseOpportunityConversationIds([
      valid,
      oldObservation,
      oldScore,
      staleServerDecision,
      missingScoreTime,
    ], NOW)).toEqual(new Set(["conversation-a"]));
  });

  it("maps every canonical next action explicitly", () => {
    const labels: Record<LeadNextAction, string> = {
      monitor: "观察",
      monitor_closely: "重点观察",
      invite_chat: "邀请咨询",
      continue_conversation: "继续会话",
      answer_shipping: "解答配送",
      answer_price: "解答价格",
      answer_payment: "解答支付",
      offer_assistance: "主动协助",
      contact_now: "立即联系",
    };

    for (const action of Object.keys(labels) as LeadNextAction[]) {
      expect(nextActionLabel(action)).toBe(labels[action]);
    }
    expect(nextActionLabel(undefined)).toBe("待确认");
  });

  it("renders commercial conversation signals as specific operator reasons", () => {
    expect(signalLabel("intent_product_price")).toBe("询问价格");
    expect(signalLabel("conversation_intent:shipping_customs")).toBe("询问关税");
    expect(signalLabel("intent_purchase_ready")).toBe("已准备购买");
    expect(purchaseOpportunitySignalLabels([
      "intent_product_price",
      "conversation_intent:product_price",
      "product_page",
    ])).toEqual(["询问价格", "商品页"]);
  });
});
