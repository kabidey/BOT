| # | Role     | Question | Got intent | Got tool | Block types | Reply len | OK |
|---|----------|----------|------------|----------|-------------|-----------|----|
| 1 | employee | What is my designation?                            | KNOWLEDGE          | answer_from_knowledge_base   | ['text'] | 205 | ✓ |
| 2 | employee | Who do I report to and who do they report to?      | DIRECTORY_QUERY    | directory_my_reporting_chain | ['text', 'reporting_chain_card'] | 32 | ✓ |
| 3 | employee | Show me my direct reports.                         | DIRECTORY_QUERY    | directory_my_team            | ['text'] | 82 | ✓ |
| 4 | employee | List all compliance department members.            | DIRECTORY_QUERY    | directory_search_employees   | ['text'] | 55 | ✓ |
| 5 | employee | Tell me about Awanish Chandra.                     | DIRECTORY_QUERY    | directory_lookup_employee    | ['text', 'directory_card'] | 60 | ✓ |
| 6 | employee | How many departments do we have?                   | DIRECTORY_QUERY    | directory_departments        | ['text', 'directory_list'] | 69 | ✓ |
| 7 | employee | List all SMIFS office locations.                   | DIRECTORY_QUERY    | directory_locations          | ['text', 'directory_list'] | 65 | ✓ |
| 8 | employee | Who joined SMIFS in the last 30 days?              | DIRECTORY_QUERY    | directory_recent_joins       | ['text', 'directory_list'] | 44 | ✓ |
| 9 | employee | What's my HRBP's name?                             | KNOWLEDGE          | answer_from_knowledge_base   | ['text'] | 48 | ✓ |
| 10 | employee | What's the minimum ticket for Mackertich ONE PMS?  | KNOWLEDGE          | answer_from_knowledge_base   | ['text'] | 147 | ✓ |
| 11 | client   | What's my risk profile?                            | KNOWLEDGE          | answer_from_knowledge_base   | ['text'] | 85 | ✓ |
| 12 | client   | Who is my relationship manager?                    | KNOWLEDGE          | answer_from_knowledge_base   | ['text'] | 117 | ✓ |
| 13 | client   | Show my equity portfolio holdings.                 | CLIENT_QUERY       | client_portfolio             | ['text'] | 203 | ✓ |
| 14 | client   | What's my account ledger balance?                  | CLIENT_QUERY       | client_ledger_balance        | ['text', 'ledger_balance_card'] | 49 | ✓ |
| 15 | client   | Show me my recent trades.                          | CLIENT_QUERY       | client_recent_trades         | ['text'] | 130 | ✓ |
| 16 | client   | When did I deposit money into my account?          | CLIENT_QUERY       | client_deposits_withdrawals  | ['text'] | 84 | ✓ |
| 17 | client   | Show me my mutual fund holdings.                   | CLIENT_QUERY       | client_mf_holdings           | ['text'] | 27 | ✓ |
| 18 | client   | What's the minimum ticket for Mackertich ONE PMS?  | ESCALATION         | answer_from_knowledge_base   | ['text', 'escalation_card'] | 136 | ✓ |
| 19 | visitor  | What is an AIF?                                    | KNOWLEDGE          | answer_from_knowledge_base   | ['text'] | 578 | ✓ |
| 20 | visitor  | What is the minimum ticket for Mackertich ONE PMS? | CALLBACK_REQUEST   | answer_from_knowledge_base   | ['text', 'form'] | 145 | ✓ |

PASS: 20/20
