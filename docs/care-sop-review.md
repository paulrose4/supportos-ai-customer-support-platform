# Care SOP Review Gate

Care instructions are executable customer guidance. They are not ordinary marketing knowledge.

The current shared review set is located in `examples/global-vault/`:

- `multi-material-product-care-handbook-review.zh-CN.md`: full issue taxonomy and human-support handbook;
- `care-sop-tpe-baseline-review.md`: TPE baseline procedure;
- `care-sop-silicone-baseline-review.md`: silicone baseline procedure;
- `care-sop-tpe-silicone-hybrid-review.md`: silicone-head/TPE-body hybrid procedure;
- `care-sop-pvc-baseline-review.md`: PVC and soft-vinyl baseline procedure.

The general handbook and four baseline SOPs were approved by `platform-owner` on 2026-07-30 under
`platform-owner-care-approval-2026-07-30`. The TPE template remains `review` / `pending_review` and
is never executable customer knowledge.

The handbook uses `category=product_care_general` and
`guidance_scope=universal_low_risk`. After approval and global synchronization it can be retrieved
from `__global__` even when a customer has not supplied a model or material. It may provide only
universal precautions and one useful clarification; material-specific treatment still requires an
applicable approved SOP.

## Required Approval

- A named supplier, product engineer, or qualified product-care reviewer confirms every step.
- The reviewer confirms applicable materials and excluded features such as heating, electronics,
  motors, removable inserts, coatings, adhesives, and painted areas.
- English and Chinese instructions are reviewed independently. Machine translation alone is not
  approval.
- Exact cleaner type, concentration, water exposure, temperature, drying, powder, oil, lubricant,
  storage posture, load limits, and prohibited actions are explicit where applicable.
- The reviewer provides the source manual, supplier bulletin, or signed approval reference.
- `approval_references` contains at least one stable source identifier or controlled document link.
- `effective_from`, optional `effective_to`, version, owner, reviewer, and `reviewed_at` are set.

## Publication Gate

1. Replace every placeholder in the review template.
2. Run `python -m pytest tests/unit/test_care_guidance.py`.
3. Change `approval_status` from `pending_review` to `approved`.
4. Change `status` from `review` to `published` only after human approval.
5. Sync global knowledge using the controlled global-sync command.
6. Run the care safety eval and manually inspect every language response.

The parser rejects a published SOP without approved status, authority level 80 or higher, a named
human reviewer, review time, applicable materials, prohibited actions, unique step IDs, and reviewed
English and Chinese instructions. Published procedures without `approval_references` are rejected.
