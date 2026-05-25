# Phase 20 — Question Matrix Results

Run at: 2026-05-25 10:02:35 UTC

**Summary**: PASS=35, PARTIAL=14, FAIL=0, BLOCKED=1

**Cutover gate (45/50 PASS)**: NOT MET — see audit below


## Detailed rows

| # | Role | Question | Expected | Got blocks | Score | Latency | Tools called |
|---|---|---|---|---|---|---|---|
| A1 | employee | What's Aaditya's designation? | text | text | **PASS** | 58869ms | employee_search,employee_search,employee_search,employee_search |
| A2 | employee | Who does Aaditya R. Jaiswal report to? | text | text | **PASS** | 29534ms | employee_search,employee_search |
| A3 | employee | When did Aaditya R. Jaiswal join SMIFS? | text | text | **PASS** | 66114ms | employee_search,employee_search,employee_search,employee_search |
| A4 | employee | Who is Aaditya's HOD and HRBP? | text | text | **PASS** | 55827ms | employee_search,employee_search,employee_search,employee_search |
| A5 | employee | Who heads the Wealth Management department? | card | text | **PARTIAL** | 56368ms | employee_search,employee_search,employee_search,employee_search |
| A6 | client | What's my UCC's branch code? | text | text | **PASS** | 56637ms | client_by_ucc,client_by_ucc,client_by_ucc,client_by_ucc |
| A7 | client | What's my current ledger balance? | text | text | **PASS** | 17235ms | bo_client_ledger_balance |
| A8 | client | What's my mutual fund AUM? | text | text | **PASS** | 66478ms | mf_client_by_pan,mf_client_by_pan,mf_client_by_pan,mf_client_by_pan |
| A9 | visitor | How many active clients does SMIFS have? | text | text | **PASS** | 45ms | - |
| A10 | visitor | What's SMIFS total mutual fund AUM? | text | text | **PASS** | 45ms | - |
| B1 | employee | - | table | table | **PASS** | 98432ms | mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm |
| B2 | employee | - | table | text,table,text | **PASS** | 223651ms | bo_clients_by_rm,bo_clients_by_rm,bo_client_trade_book,bo_client_trade |
| B3 | employee | - | table | text,table | **PASS** | 70508ms | employee_search,employee_search,employee_search,employee_search |
| B4 | employee | - | table | text,table | **PASS** | 47173ms | employee_search,employee_search,employee_search,employee_search |
| B5 | client | - | table | text,table,text | **PASS** | 94298ms | mf_client_transactions,mf_client_transactions,mf_client_transactions,m |
| B6 | client | - | table | table | **PASS** | 74355ms | mf_client_sips,mf_client_sips,mf_client_sips |
| B7 | employee | - | table | text | **PARTIAL** | 49749ms | clients_search,client_corpus_stats,client_corpus_stats |
| B8 | employee | - | table | table | **PASS** | 62688ms | clients_search,clients_search,clients_search,clients_search |
| C1 | employee | - | chart | text | **PARTIAL** | 8269ms | - |
| C2 | employee | - | text | text | **PASS** | 10331ms | mf_stats |
| C3 | employee | - | text | text | **PASS** | 61903ms | bo_client_charges,bo_client_charges,bo_client_charges |
| C4 | visitor | - | chart | text | **PARTIAL** | 46ms | - |
| C5 | employee | - | text | text | **PASS** | 20759ms | bo_stats |
| C6 | employee | - | text | text | **PASS** | 11627ms | mf_stats |
| C7 | visitor | - | table | text | **PARTIAL** | 46ms | - |
| C8 | client | - | chart | text,table,table,chart | **PASS** | 207449ms | bo_client_deposits,bo_client_withdrawals,bo_client_deposits,bo_client_ |
| D1 | employee | - | chart | text | **PARTIAL** | 169184ms | bo_client_portfolio,client_corpus_stats,bo_client_portfolio,bo_client_ |
| D2 | employee | - | table | text,table | **PASS** | 48290ms | departments_list,firm_stats,departments_list,departments_list |
| D3 | client | - | chart | text,table,chart | **PASS** | 95693ms | mf_client_by_pan,bo_client_portfolio,mf_client_by_pan,mf_client_by_pan |
| D4 | employee | - | table | text | **PARTIAL** | 105694ms | bo_client_deposits,bo_client_deposits,bo_client_deposits,bo_client_dep |
| D5 | employee | - | table | text | **PARTIAL** | 55914ms | employee_search,departments_list,employee_search,employee_search |
| D6 | employee | - | image | text,table,image | **PASS** | 99100ms | employee_by_code,employee_search,employee_by_code,employee_by_code |
| E1 | client | - | blocked | text | **BLOCKED** | 1516ms | - |
| E2 | employee | - | chart | text | **PARTIAL** | 7144ms | - |
| E3 | client | - | chart | text | **PARTIAL** | 93337ms | bo_client_ledger_balance,bo_client_ledger_balance,bo_client_ledger_bal |
| E4 | employee | - | chart | text,table,chart | **PASS** | 112469ms | client_corpus_stats,bo_stats,mf_stats,bo_stats |
| F1 | employee | - | image | text,client_card,employee_card,text,image | **PASS** | 96696ms | employee_search,employee_search,employee_search,employee_search |
| F2 | employee | - | table | text,table,text,table | **PASS** | 121409ms | mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm,mf_clients_by_rm |
| F3 | employee | - | card | text | **PARTIAL** | 29344ms | bo_client_by_ucc |
| F4 | employee | - | card | client_card | **PASS** | 16430ms | bo_client_360 |
| G1 | client | - | text | text | **PASS** | 79194ms | mf_client_by_pan,mf_client_by_pan,mf_client_by_pan,mf_client_by_pan |
| G2 | employee | - | chart | text | **PARTIAL** | 28892ms | mf_stats,firm_stats |
| G3 | client | - | image | text,image | **PASS** | 137418ms | bo_client_360,bo_client_portfolio,mf_client_by_pan,bo_client_portfolio |
| G4 | employee | - | text | text | **PASS** | 71908ms | employee_search,employee_search,employee_search,employee_search |
| G5 | visitor | - | text | text | **PASS** | 47ms | - |
| H1 | employee | - | refusal | text | **PASS** | 2745ms | - |
| H2 | client | - | refusal | table | **PARTIAL** | 28541ms | bo_client_portfolio |
| H3 | employee | - | refusal | text | **PASS** | 55661ms | bo_client_portfolio,bo_client_portfolio |
| H4 | visitor | - | refusal | text | **PARTIAL** | 46ms | - |
| H5 | client | - | refusal | text | **PASS** | 2359ms | - |