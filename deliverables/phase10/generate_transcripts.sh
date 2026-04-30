#!/bin/bash
# Phase 10 deliverable generator.
# Produces transcripts for client / employee / visitor flows.

set -e
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
OUT=/app/deliverables/phase10
mkdir -p "$OUT"

turn() {
  # $1 = sid, $2 = message, $3 = label-out-file
  local sid="$1" msg="$2" out="$3"
  curl -s -X POST "$API_URL/api/agent/turn" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$sid\",\"message\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$msg")}" \
    > "$out"
}

role() {
  local sid="$1" role_name="$2"
  curl -s -X POST "$API_URL/api/sessions/$sid/select_role" \
    -H "Content-Type: application/json" \
    -d "{\"role\":\"$role_name\"}"
}

# ------------------------- CLIENT FLOW -------------------------
SID_C="deliv-client-$(date +%s)"
echo "Client SID: $SID_C" > "$OUT/transcript_client.txt"
echo "=== STEP 1: select_role client ===" >> "$OUT/transcript_client.txt"
role "$SID_C" "client" >> "$OUT/transcript_client.txt"
echo -e "\n\n=== STEP 2: UCC 63876 ===" >> "$OUT/transcript_client.txt"
turn "$SID_C" "63876" /tmp/t_c1.json
cat /tmp/t_c1.json >> "$OUT/transcript_client.txt"
echo -e "\n\n=== STEP 3: PAN ARIPP3602Q (verification) ===" >> "$OUT/transcript_client.txt"
turn "$SID_C" "ARIPP3602Q" /tmp/t_c2.json
cat /tmp/t_c2.json >> "$OUT/transcript_client.txt"

# 5 verified client self-queries
QS=(
  "What is my risk profile?"
  "Who is my relationship manager?"
  "What segments am I active in?"
  "What city is my branch in?"
  "What is my account status?"
)
for i in "${!QS[@]}"; do
  echo -e "\n\n=== STEP $((4+i)): ${QS[$i]} ===" >> "$OUT/transcript_client.txt"
  turn "$SID_C" "${QS[$i]}" /tmp/t_cs.json
  cat /tmp/t_cs.json >> "$OUT/transcript_client.txt"
done

# Product fallback
echo -e "\n\n=== STEP 9: Product question Alchemy Smart Alpha (non-keyword fund) ===" >> "$OUT/transcript_client.txt"
turn "$SID_C" "What is the historical NAV of Alchemy Smart Alpha?" /tmp/t_cp1.json
cat /tmp/t_cp1.json >> "$OUT/transcript_client.txt"

echo -e "\n\n=== STEP 10: Product question Mackertich ONE PMS ===" >> "$OUT/transcript_client.txt"
turn "$SID_C" "What is the minimum ticket size for Mackertich ONE PMS?" /tmp/t_cp2.json
cat /tmp/t_cp2.json >> "$OUT/transcript_client.txt"

echo -e "\n\n=== STEP 11: /api/sessions/$SID_C snapshot (auth_state=verified, role=client) ===" >> "$OUT/transcript_client.txt"
curl -s "$API_URL/api/sessions/$SID_C" >> "$OUT/transcript_client.txt"

# ------------------------- VISITOR FLOW -------------------------
SID_V="deliv-visitor-$(date +%s)"
echo "Visitor SID: $SID_V" > "$OUT/transcript_visitor.txt"
echo "=== STEP 1: select_role visitor ===" >> "$OUT/transcript_visitor.txt"
role "$SID_V" "visitor" >> "$OUT/transcript_visitor.txt"
echo -e "\n\n=== STEP 2: Product Q — PMS minimum (should get callback form) ===" >> "$OUT/transcript_visitor.txt"
turn "$SID_V" "What is the minimum investment for Mackertich ONE PMS?" /tmp/t_v1.json
cat /tmp/t_v1.json >> "$OUT/transcript_visitor.txt"
echo -e "\n\n=== STEP 3: General small talk ===" >> "$OUT/transcript_visitor.txt"
turn "$SID_V" "Who is SMIFS?" /tmp/t_v2.json
cat /tmp/t_v2.json >> "$OUT/transcript_visitor.txt"

# ------------------------- EMPLOYEE FLOW -------------------------
SID_E="deliv-emp-$(date +%s)"
echo "Employee SID: $SID_E" > "$OUT/transcript_employee.txt"
echo "=== STEP 1: select_role employee ===" >> "$OUT/transcript_employee.txt"
role "$SID_E" "employee" >> "$OUT/transcript_employee.txt"
echo -e "\n\n=== STEP 2: Email ===" >> "$OUT/transcript_employee.txt"
turn "$SID_E" "aaditya.jaiswal@smifs.com" /tmp/t_e1.json
cat /tmp/t_e1.json >> "$OUT/transcript_employee.txt"
echo -e "\n\n=== STEP 3: PAN BQPPJ8323M (verification) ===" >> "$OUT/transcript_employee.txt"
turn "$SID_E" "BQPPJ8323M" /tmp/t_e2.json
cat /tmp/t_e2.json >> "$OUT/transcript_employee.txt"
echo -e "\n\n=== STEP 4: Product Q — should retrieve smifs_knowledge ===" >> "$OUT/transcript_employee.txt"
turn "$SID_E" "What is the minimum ticket size for Mackertich ONE PMS?" /tmp/t_e3.json
cat /tmp/t_e3.json >> "$OUT/transcript_employee.txt"

# Sample injected CLIENT_PROFILE (reconstructed from identity)
echo -e "\n=== Sample CLIENT_PROFILE injected at system-prompt time (UCC 63876) ===" > "$OUT/sample_client_profile_injection.txt"
python3 - <<'PY' >> "$OUT/sample_client_profile_injection.txt"
import json, sys, os
sys.path.insert(0, '/app/backend')
# Pull a client record from identity's orglens helper and build the injected block
# without hitting live network again — reuse the session we just verified.
from pymongo import MongoClient
mc = MongoClient(os.environ["MONGO_URL"])
db = mc[os.environ.get("DB_NAME","smifs")]
sid = [s for s in db.sessions.find().sort("_id", -1).limit(40)
       if s.get("session_type") == "client" and s.get("auth_state") == "verified"][0]["_id"]
doc = db.sessions.find_one({"_id": sid})
ident = (doc or {}).get("identity", {}).get("raw") or {}
import identity as I
block = I.context_block_for(ident) if hasattr(I, "context_block_for") else None
print("--- identity.raw (masked fields already omitted) ---")
print(json.dumps(ident, indent=2, default=str))
print("\n--- Injected system-prompt block ---")
from agents.auth_agent import context_block_for as cbf
print(cbf(ident) or "(empty)")
PY

echo "DONE"
ls -la "$OUT"
