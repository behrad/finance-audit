---
name: payhawk-default
model: gemini-3.5-flash   # verified in the AI Studio model list; 3.8-flash returned 503 "high demand"
flags: [duplicate, fx_difference, wrong_value, total_mismatch, note_mismatch,
        supplier_not_employee, category_mismatch, date_mismatch,
        service_period_out_of_range, number_mismatch, unreadable_receipt]
checks:                      # deterministic checks (app/pipeline/checks.py), run in this order
  readable: {min_legibility: 0.5}
  duplicate: {}
  total_matches_receipt: {}
  value_matches: {tolerance: 0.01}
  fx_deviation: {tolerance: 0.02, reference_rates: {EUR: 1.09, GBP: 1.27}, flag: fx_difference}   # static table, D3: no external FX API
  supplier_is_employee: {}
  document_date_matches: {}
  number_matches: {}
  service_period: {max_future_days: 30}
  auto_approve_under: {amount: 10}   # in the account's base currency (Q7)
categories: [Meals, Travel, Accommodation, Office supplies, Software, Equipment, Learning, Other]
fallback_category: Other
---
check whether there is any duplicate claims, if there are, mark the second one as duplicate, the first one as original
for values less than 10$, confirm automatically
the value on the receipt should match with the value that's filled by the user in the form (you might need to exchange currency values, it's acceptable as long as it has a deviation of 2%, otherwise, raise flag fx_difference)
Align "Note" field with the content on the invoice. they should match, for example, a receipt of food should align with Note about food, not flight
supplier field always needs to be the name of the employee, who is claiming the expense
category should be compatible with the content of the invoice, if no proper allocation can be allocated, then allocate to other bucket. if you need to change the category and find a more proper one, change it.
document date should match with the date on the invoice
service period should always be within the current month, also not more than 30 days in the future.
number field should match the invoice number
check the Total value, should match the final value of the invoice
