---
document_id: care-sop-tpe-template
tenant_id: __global__
title: TPE Care SOP Supplier Review Template
category: product_care_sop
audience: public
product: all
region: global
language: en
status: review
authority_level: 90
priority: 90
version: "0.1.0"
effective_from: null
effective_to: null
owner_role: product_safety_owner
reviewer: unassigned
reviewed_at: null
updated_at: "2026-07-20T00:00:00Z"
approval_status: pending_review
approval_references: []
procedure_id: care.tpe.template.v0
applicable_materials:
  - tpe
prohibited_actions:
  - unapproved_cleaner
  - unapproved_powder
  - unapproved_oil
  - alcohol
  - bleach
  - strong_acid_or_alkali
  - unapproved_heat
  - soaking
  - self_repair
approved_steps:
  - step_id: care.tpe.supplier-step-1
    instructions:
      en: "[SUPPLIER REVIEW REQUIRED: replace with an approved English instruction]"
      zh: "[需要供应商审核：请替换为审核通过的中文步骤]"
---
# Not approved for customer answers

This document is a review template. It remains excluded from retrieval while `status` is `review`
and `approval_status` is `pending_review`.

Before publication, replace every placeholder with supplier-approved wording, add the named human
reviewer and review timestamp, set an effective period, and complete the checklist in
`docs/care-sop-review.md`.
