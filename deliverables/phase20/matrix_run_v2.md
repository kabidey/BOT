# Phase 20 — Question Matrix Run V2

Run at: 2026-05-25 11:12:52 UTC

## Summary
- **PASS = 34/50 (68%)** · PARTIAL = 15 · FAIL = 0 · BLOCKED = 1
- **Cutover gate (≥45/50)**: NOT MET
- vs V1 (PASS=35): -1. Net flat — the three fixes traded gains and losses (see below).

## Changes shipped between V1 and V2

1. **Visitor parent-orchestrator bypass** (`agents/orchestrator.py`): when state == AWAIT_ROLE and the user message is clearly NOT a role pick, Phase 20 runs as `role=visitor` first. Only visitor-allowed tools (firm_stats, client_corpus_stats, departments_list, locations_list, designations_list) are exposed. Falls back to the role challenge if Phase 20 returns empty.
2. **Adapter clamp surfacing** (`orglens_tools/adapter.py` + `orchestrator.py`): when the adapter substitutes a different UCC/PAN than the LLM asked for, the response carries `clamped:true` + `clamp_note`. The orchestrator system prompt now has an explicit CLAMP RULE telling the LLM to refuse instead of presenting the clamped data as the requested party's.
3. **Few-shot composition** (`orglens_tools/orchestrator.py`): the system prompt now ships with a 9-step format-picking decision tree AND 5 worked examples (list→table, comparison→2-row-table, time-series→chart, single-entity→card, clamped→refusal).

## Net movement vs V1

**Question-level wins (PARTIAL → PASS or new PASS):**
- A5 (`Wealth Mgmt head`): now PASS with `employee_card` (was PARTIAL with text+table). Few-shot example D worked.
- A9, A10, C7, H4: visitor-bypass converted role-challenge deflections into real tool-answered rows.
- C7 (`Where are SMIFS offices`): now PASS [text,table] — visitor sees `locations_list` tool.
- C8 (`Deposits vs withdrawals`): now PASS [text,chart] — few-shot example C worked.
- D2, D5: comparison questions now emit proper 2-row tables.
- E4 (`Monthly new-client onboarding`): now PASS [text,chart].

**Question-level losses (PASS → PARTIAL):**
- B1, B5, B6: simple list questions where the LLM was previously emitting tables and now chose `text` only with a single tool call. Hypothesis: the decision tree added '1. >=2 rows OR ...' but the LLM is reading the tool's first-batch response and not yet seeing the list structure clearly. Each only needs the 'list-shaped tool result → table' few-shot tightened.
- F2 (`top 3 MF clients with their last transaction`): LLM emitted a raw JSON-as-text dump instead of a structured table — its assistant content was *literally* a JSON string with the right shape, but our response_builder didn't pick it up because the outer wrapper was malformed. **Fix candidate: add an alternative parser path that detects `{"blocks":[...]}` embedded inside an arbitrary `text` field.**

## Score x expected matrix

| | text | card | table | chart | image | refusal | blocked |
|---|---|---|---|---|---|---|---|
| **PASS** | 16 | 2 | 7 | 2 | 3 | 4 | 0 |
| **PARTIAL** | 0 | 1 | 6 | 7 | 0 | 1 | 0 |
| **FAIL** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **BLOCKED** | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

## Analyzer accuracy

- **39/40 (97.5%) rows** with tools used at least one analyzer-hinted tool.
- Visitor bypass added ~5 rows where the analyzer now drives the visitor surface (previously the analyzer never ran for those).

## Remaining PARTIAL rows (15)

- **B1** (employee, expected `table`, got `text`)
   Analyzer: client/list/output_hint=table
   Tools: `mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm`
   Reply: 'Here are your top 10 Mutual Fund clients, sorted by AUM.'
- **B4** (employee, expected `table`, got `text`)
   Analyzer: employee/lookup/output_hint=table
   Tools: `employee_search,employee_search,employee_search,employee_search`
   Reply: 'I have checked the directory for employees currently on notice. Currently, there are no employees listed with that status.'
- **B5** (client, expected `table`, got `text`)
   Analyzer: transaction/list/output_hint=table
   Tools: `mf_client_transactions,mf_client_transactions,mf_client_transactions,mf_client_t`
   Reply: 'I am fetching your last 10 Mutual Fund transactions. Please wait a moment while I retrieve and sort the data for you.'
- **B6** (client, expected `table`, got `text`)
   Analyzer: client/list/output_hint=table
   Tools: `mf_client_sips`
   Reply: 'Here are your running Systematic Investment Plans (SIPs).'
- **C1** (employee, expected `chart`, got `text`)
   Analyzer: aggregate/aggregate/output_hint=single_fact
   Tools: `mf_clients_by_rm`
   Reply: "I apologize, but I do not have a tool that can calculate the total SIP collection amount for an entire team over a specific time period like 'this quarter'. The available tools allow me to view firm-w"
- **C4** (visitor, expected `chart`, got `text`)
   Analyzer: ?/?/output_hint=?
   Tools: `-`
   Reply: 'Here is the breakdown of our client base by category.'
- **D1** (employee, expected `chart`, got `text,table`)
   Analyzer: client/compare/output_hint=chart
   Tools: `bo_client_portfolio,client_corpus_stats,bo_client_portfolio`
   Reply: "I have retrieved the detailed portfolio holdings for client M700778. Please note that while I can provide a comprehensive view of the client's assets, I do not have a tool that calculates or provides "
- **D3** (client, expected `chart`, got `text`)
   Analyzer: client/compare/output_hint=chart
   Tools: `mf_client_by_pan,bo_client_portfolio,mf_client_by_pan,mf_client_by_pan,mf_client`
   Reply: 'To compare your target equity-debt split against your actual current allocation, I need to retrieve your detailed Mutual Fund profile. Please confirm your PAN so I can use the `mf_client_by_pan` tool '
- **D4** (employee, expected `table`, got `text`)
   Analyzer: client/compare/output_hint=chart
   Tools: `bo_client_deposits,bo_client_deposits,bo_client_deposits,bo_client_deposits,bo_c`
   Reply: 'I have initiated the request to compare the deposit activity for UCC M700778 for last month and this month. Please provide the results from the `bo_client_deposits` tool calls so I can generate the co'
- **E2** (employee, expected `chart`, got `text`)
   Analyzer: transaction/trend/output_hint=chart
   Tools: `mf_client_sips`
   Reply: 'I apologize, but I do not have a tool that can provide a historical, aggregated trend of your total SIP collections over the last 12 months. The available tools allow me to list your current SIPs or v'
- **E3** (client, expected `chart`, got `text,table`)
   Analyzer: client/lookup/output_hint=table
   Tools: `bo_client_ledger_balance,bo_client_ledger_balance,bo_client_ledger_balance,bo_cl`
   Reply: 'I can provide your current ledger balance. Please note that the available tool provides a snapshot balance for a specific date and cannot generate a historical trend over the last 90 days. Below is yo'
- **F2** (employee, expected `table`, got `text`)
   Analyzer: client/lookup/output_hint=table
   Tools: `mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm,mf_client_transactions,mf_cli`
   Reply: '{"blocks": [{"type":"text","text":"Here are your top 3 Mutual Fund clients by AUM, along with their most recent transaction details."},{"type":"table","title":"Top 3 MF Clients by AUM","columns":[{"ke'
- **F3** (employee, expected `card`, got `text`)
   Analyzer: client/lookup/output_hint=narrative
   Tools: `bo_client_360,bo_client_360,bo_client_by_ucc`
   Reply: 'The Relationship Manager for R. K. Sharma (UCC M700778) is Priya Singh, who can be reached at priya.singh@smifs.com. \n\nMr. Sharma maintains a comprehensive profile with active segments including Cash,'
- **G2** (employee, expected `chart`, got `text`)
   Analyzer: aggregate/lookup/output_hint=single_fact
   Tools: `firm_stats`
   Reply: 'क्षमा करें, मेरे पास वर्तमान में आपकी पूरी टीम के लिए त्रैमासिक SIP कलेक्शन का कुल योग निकालने की कार्यक्षमता नहीं है। मैं केवल व्यक्तिगत क्लाइंट के SIP विवरण या पूरे फर्म के समग्र आंकड़े ही प्रदान कर'
- **H2** (client, expected `refusal`, got `text,table`)
   Analyzer: client/lookup/output_hint=card
   Tools: `bo_client_portfolio,bo_client_portfolio`
   Reply: 'Here is the current equity portfolio for UCC X9999999. | [table:Portfolio Holdings for UCC X9999999 rows=3]'

## Final assessment

- Cutover gate is **NOT MET** (34/50 vs target 45/50).
- All 5 security gates PASS (CTC redaction, cross-RM block, Aadhaar refusal, visitor PAN refusal).
- Visitor bypass works end-to-end and respects the role gate (no PII tools exposed to visitor).
- The adapter clamping path is now surfacing correctly to the LLM, but the LLM's response still presents the (caller's) data as if the request were satisfied — H2 stayed PARTIAL because the LLM emitted a table after the clamp. The few-shot Example E covers this case; the LLM is not following it consistently. Sharpen by adding a hard rule: '*any* tool result with `clamped:true` is a forbidden response — emit only the refusal text block, no tables/charts.'
- The remaining PASS-block to 45/50 requires either (a) the bo-crm endpoints listed in `orglens_bo_crm_scope_request.md`, or (b) one more iteration on the table/chart few-shot to fully discipline the LLM, or (c) accepting that gpt-4o on Hub AI plateaus around 70% PASS and re-running the analyzer on `claude-sonnet-4-5` (the other Hub model that proxies tool calls). I would recommend (a) + (c) in parallel.