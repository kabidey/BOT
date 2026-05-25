# Phase 20 — Question Matrix Audit (Analyzer accuracy)

Run at: 2026-05-25 11:09:41 UTC

**Summary**: PASS=34, PARTIAL=15, FAIL=0, BLOCKED=1


**Question Analyzer hint coverage**: 39/43 (90.7% if runnable else 0%) rows had at least one analyzer-hinted tool actually used.


| # | Role | Score | Expected | Got | Analyzer entity/op/out | Analyzer tool_hint | Tools called | Hint matched |
|---|---|---|---|---|---|---|---|---|
| A1 | employee | **PASS** | text | text | employee/lookup/single_fact | employee_by_code,employee_search | employee_search,employee_search,employee_search,employee_sea | yes |
| A2 | employee | **PASS** | text | text | employee/lookup/single_fact | employee_by_code,employee_search,org_tree | employee_search,employee_search | yes |
| A3 | employee | **PASS** | text | text | employee/lookup/single_fact | employee_by_code,employee_search | employee_search,employee_search,employee_search,employee_sea | yes |
| A4 | employee | **PASS** | text | text | employee/lookup/single_fact | employee_by_code,employee_search,org_tree | employee_search,employee_search,employee_search,employee_sea | yes |
| A5 | employee | **PASS** | card | employee_card | employee/lookup/single_fact | employee_by_code,employee_search,departments_list | employee_search,employee_search,employee_search | yes |
| A6 | client | **PASS** | text | text | client/lookup/single_fact | client_by_ucc,bo_client_by_ucc | bo_client_by_ucc,bo_client_by_ucc,bo_client_by_ucc,bo_client | yes |
| A7 | client | **PASS** | text | text | client/lookup/single_fact | bo_client_ledger_balance | bo_client_ledger_balance,bo_client_ledger_balance | yes |
| A8 | client | **PASS** | text | text,table | client/lookup/single_fact | mf_client_by_pan,mf_client_folios | mf_client_by_pan,mf_client_by_pan,mf_client_by_pan,mf_client | yes |
| A9 | visitor | **PASS** | text | text | ?/?/? | - | - | no |
| A10 | visitor | **PASS** | text | text | ?/?/? | - | - | no |
| B1 | employee | **PARTIAL** | table | text | client/list/table | mf_clients_by_rm,mf_client_by_pan | mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm,mf_client | yes |
| B2 | employee | **PASS** | table | text,table | client/list/table | bo_clients_by_rm,bo_client_trade_book | bo_clients_by_rm,bo_clients_by_rm,bo_clients_by_rm,bo_client | yes |
| B3 | employee | **PASS** | table | text,table | employee/lookup/image | org_tree,departments_list,employee_search | employee_search,employee_search,employee_search | yes |
| B4 | employee | **PARTIAL** | table | text | employee/lookup/table | employee_search,org_tree | employee_search,employee_search,employee_search,employee_sea | yes |
| B5 | client | **PARTIAL** | table | text | transaction/list/table | mf_client_transactions | mf_client_transactions,mf_client_transactions,mf_client_tran | yes |
| B6 | client | **PARTIAL** | table | text | client/list/table | mf_client_sips,mf_client_folios,mf_client_transactions | mf_client_sips | yes |
| B7 | employee | **PASS** | table | text,table | client/aggregate/single_fact | client_corpus_stats,locations_list,clients_search | clients_search,clients_search | yes |
| B8 | employee | **PASS** | table | text,table | client/list/table | bo_client_by_ucc,clients_search | clients_search,clients_search,clients_search,clients_search | yes |
| C1 | employee | **PARTIAL** | chart | text | aggregate/aggregate/single_fact | mf_client_sips,mf_clients_by_rm,mf_stats | mf_clients_by_rm | yes |
| C2 | employee | **PASS** | text | text,table | aggregate/lookup/single_fact | mf_stats,firm_stats | mf_stats | yes |
| C3 | employee | **PASS** | text | text,table | client/lookup/single_fact | bo_client_by_ucc,bo_client_charges | bo_client_charges,bo_client_charges,bo_client_charges,bo_cli | yes |
| C4 | visitor | **PARTIAL** | chart | text | ?/?/? | - | - | no |
| C5 | employee | **PASS** | text | text | transaction/aggregate/single_fact | bo_client_ledger_balance,bo_stats | bo_stats,bo_stats | yes |
| C6 | employee | **PASS** | text | text,table | aggregate/lookup/single_fact | mf_stats,firm_stats | mf_stats | yes |
| C7 | visitor | **PASS** | table | text,table | ?/?/? | - | - | no |
| C8 | client | **PASS** | chart | text,chart | client/aggregate/chart | bo_client_deposits,bo_client_withdrawals,bo_client_ledger_ba | bo_client_deposits,bo_client_withdrawals,bo_client_deposits, | yes |
| D1 | employee | **PARTIAL** | chart | text,table | client/compare/chart | bo_client_portfolio,client_by_ucc,firm_stats | bo_client_portfolio,client_corpus_stats,bo_client_portfolio | yes |
| D2 | employee | **PASS** | table | text,table | aggregate/compare/table | departments_list,locations_list,firm_stats | departments_list,departments_list,departments_list,departmen | yes |
| D3 | client | **PARTIAL** | chart | text | client/compare/chart | mf_client_by_pan,bo_client_portfolio,mf_client_folios | mf_client_by_pan,bo_client_portfolio,mf_client_by_pan,mf_cli | yes |
| D4 | employee | **PARTIAL** | table | text | client/compare/chart | bo_client_deposits,bo_client_by_ucc | bo_client_deposits,bo_client_deposits,bo_client_deposits,bo_ | yes |
| D5 | employee | **PASS** | table | text,table | employee/compare/table | employee_by_code,employee_search,departments_list | employee_search,departments_list,employee_search,employee_se | yes |
| D6 | employee | **PASS** | image | text,table,image | employee/compare/image | org_tree,employee_by_code | employee_by_code,employee_by_code,employee_by_code,employee_ | yes |
| E1 | client | **BLOCKED** | blocked | text | ?/?/? | - | - | no |
| E2 | employee | **PARTIAL** | chart | text | transaction/trend/chart | mf_client_sips,mf_client_transactions | mf_client_sips | yes |
| E3 | client | **PARTIAL** | chart | text,table | client/lookup/table | bo_client_ledger_balance | bo_client_ledger_balance,bo_client_ledger_balance,bo_client_ | yes |
| E4 | employee | **PASS** | chart | text,chart | aggregate/lookup/chart | client_corpus_stats,bo_stats,mf_stats | client_corpus_stats,bo_stats | yes |
| F1 | employee | **PASS** | image | employee_card,text,image | employee/lookup/image | org_tree,employee_by_code,employee_search | employee_search,employee_search,employee_search,employee_sea | yes |
| F2 | employee | **PARTIAL** | table | text | client/lookup/table | mf_clients_by_rm,mf_client_transactions | mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm,mf_client | yes |
| F3 | employee | **PARTIAL** | card | text | client/lookup/narrative | bo_client_by_ucc,bo_client_360,clients_search | bo_client_360,bo_client_360,bo_client_by_ucc | yes |
| F4 | employee | **PASS** | card | client_card,text | client/lookup/card | bo_client_by_ucc,bo_client_360,bo_client_portfolio | bo_client_360,bo_client_360 | yes |
| G1 | client | **PASS** | text | text | client/lookup/single_fact | mf_client_by_pan,mf_client_folios | mf_client_by_pan | yes |
| G2 | employee | **PARTIAL** | chart | text | aggregate/lookup/single_fact | mf_stats,mf_client_sips,bo_stats | firm_stats | no |
| G3 | client | **PASS** | image | text,image | client/lookup/image | mf_client_by_pan,bo_client_portfolio,mf_client_folios | client_corpus_stats,mf_client_by_pan,mf_client_by_pan,mf_cli | yes |
| G4 | employee | **PASS** | text | text | employee/lookup/single_fact | employee_by_code,employee_search,departments_list | employee_search,employee_search,employee_search,employee_sea | yes |
| G5 | visitor | **PASS** | text | text | ?/?/? | - | - | no |
| H1 | employee | **PASS** | refusal | text | employee/lookup/refusal | - | - | no |
| H2 | client | **PARTIAL** | refusal | text,table | client/lookup/card | bo_client_by_ucc,bo_client_portfolio | bo_client_portfolio,bo_client_portfolio | yes |
| H3 | employee | **PASS** | refusal | text | client/lookup/card | bo_client_by_ucc,bo_client_360,bo_client_portfolio | - | no |
| H4 | visitor | **PASS** | refusal | text | ?/?/? | - | - | no |
| H5 | client | **PASS** | refusal | text | client/lookup/refusal | - | - | no |