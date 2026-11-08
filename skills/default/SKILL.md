---
name: payhawk-default
model: gemini-3.5-flash   # verified in the AI Studio model list; 3.8-flash returned 503 "high demand"
flags: [duplicate, wrong_exchange_rate, wrong_value, total_mismatch, note_mismatch,
        supplier_not_employee, category_mismatch, date_mismatch,
        service_period_out_of_range, number_mismatch, unreadable_receipt]
checks:                      # deterministic checks (app/pipeline/checks.py), run in this order
  readable: {min_legibility: 0.5}
  duplicate: {}
  total_matches_receipt: {}
  value_matches: {tolerance: 0.01}
  fx_deviation: {tolerance: 0.02, reference_rates: {EUR: 1.09, GBP: 1.27}}   # static table, D3: no external FX API
  supplier_is_employee: {}
  document_date_matches: {}
  number_matches: {}
  service_period: {max_future_days: 30}
  auto_approve_under: {amount: 10}   # in the account's base currency (Q7)
categories: [Meals, Travel, Accommodation, Office supplies, Software, Equipment, Learning, Other]
fallback_category: Other
---
You review one expense claim against its receipt for a finance team.

You are given the Payhawk expense, the data extracted from the receipt, and the results of
automatic checks (arithmetic, dates, numbers, duplicates, supplier). Do NOT redo or dispute those.
Judge only what needs meaning:

1. note_mismatch: the expense note/title does not describe what the receipt is for
   (e.g. note "Flight to Berlin" on a restaurant receipt). Wording differences are fine.
2. category_mismatch: the expense category does not fit what was bought. If it does not fit,
   set category_fix to the best category from the allowed list; otherwise leave it empty.

For each flag give a one-sentence description a finance person can act on.
If nothing is wrong, return no flags. Never invent other flags.
