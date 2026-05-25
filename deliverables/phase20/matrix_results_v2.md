# Phase 20 — Question Matrix V2 Results

Run at: 2026-05-25 11:12:52 UTC

**Summary**: PASS=34, PARTIAL=15, FAIL=0, BLOCKED=1

**Cutover gate (>=45/50 PASS)**: NOT MET


## Per-row results

| # | Role | Expected | Got | Score | Tools | Latency |
|---|---|---|---|---|---|---|
| A1 | employee | text | text | **PASS** | employee_search,employee_search,employee_search | 69076ms |
| A2 | employee | text | text | **PASS** | employee_search,employee_search | 50617ms |
| A3 | employee | text | text | **PASS** | employee_search,employee_search,employee_search | 63360ms |
| A4 | employee | text | text | **PASS** | employee_search,employee_search,employee_search | 51993ms |
| A5 | employee | card | employee_card | **PASS** | employee_search,employee_search,employee_search | 50861ms |
| A6 | client | text | text | **PASS** | bo_client_by_ucc,bo_client_by_ucc,bo_client_by_ucc | 110174ms |
| A7 | client | text | text | **PASS** | bo_client_ledger_balance,bo_client_ledger_balance | 39933ms |
| A8 | client | text | text,table | **PASS** | mf_client_by_pan,mf_client_by_pan,mf_client_by_pan | 99684ms |
| A9 | visitor | text | text | **PASS** | - | 11133ms |
| A10 | visitor | text | text | **PASS** | - | 7242ms |
| B1 | employee | table | text | **PARTIAL** | mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm | 63493ms |
| B2 | employee | table | text,table | **PASS** | bo_clients_by_rm,bo_clients_by_rm,bo_clients_by_rm | 115054ms |
| B3 | employee | table | text,table | **PASS** | employee_search,employee_search,employee_search | 60879ms |
| B4 | employee | table | text | **PARTIAL** | employee_search,employee_search,employee_search | 71077ms |
| B5 | client | table | text | **PARTIAL** | mf_client_transactions,mf_client_transactions,mf_c | 87822ms |
| B6 | client | table | text | **PARTIAL** | mf_client_sips | 21435ms |
| B7 | employee | table | text,table | **PASS** | clients_search,clients_search | 70285ms |
| B8 | employee | table | text,table | **PASS** | clients_search,clients_search,clients_search | 75508ms |
| C1 | employee | chart | text | **PARTIAL** | mf_clients_by_rm | 42304ms |
| C2 | employee | text | text,table | **PASS** | mf_stats | 22471ms |
| C3 | employee | text | text,table | **PASS** | bo_client_charges,bo_client_charges,bo_client_char | 107557ms |
| C4 | visitor | chart | text | **PARTIAL** | - | 19546ms |
| C5 | employee | text | text | **PASS** | bo_stats,bo_stats | 44556ms |
| C6 | employee | text | text,table | **PASS** | mf_stats | 23763ms |
| C7 | visitor | table | text,table | **PASS** | - | 15618ms |
| C8 | client | chart | text,chart | **PASS** | bo_client_deposits,bo_client_withdrawals,bo_client | 93940ms |
| D1 | employee | chart | text,table | **PARTIAL** | bo_client_portfolio,client_corpus_stats,bo_client_ | 75533ms |
| D2 | employee | table | text,table | **PASS** | departments_list,departments_list,departments_list | 64038ms |
| D3 | client | chart | text | **PARTIAL** | mf_client_by_pan,bo_client_portfolio,mf_client_by_ | 87369ms |
| D4 | employee | table | text | **PARTIAL** | bo_client_deposits,bo_client_deposits,bo_client_de | 99153ms |
| D5 | employee | table | text,table | **PASS** | employee_search,departments_list,employee_search | 105226ms |
| D6 | employee | image | text,table,image | **PASS** | employee_by_code,employee_by_code,employee_by_code | 132790ms |
| E1 | client | blocked | text | **BLOCKED** | - | 1348ms |
| E2 | employee | chart | text | **PARTIAL** | mf_client_sips | 36331ms |
| E3 | client | chart | text,table | **PARTIAL** | bo_client_ledger_balance,bo_client_ledger_balance, | 113274ms |
| E4 | employee | chart | text,chart | **PASS** | client_corpus_stats,bo_stats | 79272ms |
| F1 | employee | image | employee_card,text,image | **PASS** | employee_search,employee_search,employee_search | 91845ms |
| F2 | employee | table | text | **PARTIAL** | mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm | 192344ms |
| F3 | employee | card | text | **PARTIAL** | bo_client_360,bo_client_360,bo_client_by_ucc | 79353ms |
| F4 | employee | card | client_card,text | **PASS** | bo_client_360,bo_client_360 | 34923ms |
| G1 | client | text | text | **PASS** | mf_client_by_pan | 35191ms |
| G2 | employee | chart | text | **PARTIAL** | firm_stats | 36751ms |
| G3 | client | image | text,image | **PASS** | client_corpus_stats,mf_client_by_pan,mf_client_by_ | 94092ms |
| G4 | employee | text | text | **PASS** | employee_search,employee_search,employee_search | 52865ms |
| G5 | visitor | text | text | **PASS** | - | 14250ms |
| H1 | employee | refusal | text | **PASS** | - | 2985ms |
| H2 | client | refusal | text,table | **PARTIAL** | bo_client_portfolio,bo_client_portfolio | 71316ms |
| H3 | employee | refusal | text | **PASS** | - | 38617ms |
| H4 | visitor | refusal | text | **PASS** | - | 30165ms |
| H5 | client | refusal | text | **PASS** | - | 2652ms |