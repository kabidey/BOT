#!/usr/bin/env bash
# Phase 19 — Office 365 SMTP bootstrap for the SMIFS production backend.
#
# Run this ONCE inside the production backend container (or wherever
# `/app/backend/.env` lives). Idempotent: re-running upserts the same keys
# instead of duplicating them.
#
# The script writes credentials to /app/backend/.env, restarts the backend
# (so all workers pick up the new env), and validates the /status endpoint.
# It then attempts a live send against the most recent sale that hasn't yet
# been delivered (or, failing that, SALE-2026-0018 as the canary).
#
# Single-use. Delete after you've run it.
set -euo pipefail

ENV_FILE="/app/backend/.env"
ADMIN_TOKEN="${ADMIN_TOKEN_OVERRIDE:-$(grep -E '^ADMIN_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')}"
API_BASE="${API_BASE:-http://localhost:8001}"

declare -A KV=(
  [SMTP_HOST]="smtp.office365.com"
  [SMTP_PORT]="587"
  [SMTP_STARTTLS]="true"
  [SMTP_USER]="wealth.guidance@smifs.com"
  [SMTP_PASSWORD]="Kutta@123"
  [FROM_EMAIL]="wealth.guidance@smifs.com"
  [FROM_NAME]="SMIFS Wealth Guidance"
  [CC_OPS_FIXED]="ho.operations@smifs.com,insurance.bpo@smifs.com,fundaccounting@smifs.com,bi@smifs.com"
)

# 1. Upsert keys into .env (idempotent).
TMP="$(mktemp)"
touch "$ENV_FILE"
declare -A SEEN=()
while IFS= read -r line; do
  if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "${line// }" ]] || [[ "$line" != *=* ]]; then
    printf '%s\n' "$line" >>"$TMP"; continue
  fi
  key="${line%%=*}"; key="${key// }"
  if [[ -n "${KV[$key]+x}" ]]; then
    printf '%s=%s\n' "$key" "${KV[$key]}" >>"$TMP"
    SEEN[$key]=1
  else
    printf '%s\n' "$line" >>"$TMP"
  fi
done <"$ENV_FILE"
{
  appended=0
  for k in SMTP_HOST SMTP_PORT SMTP_STARTTLS SMTP_USER SMTP_PASSWORD FROM_EMAIL FROM_NAME CC_OPS_FIXED; do
    if [[ -z "${SEEN[$k]:-}" ]]; then
      if [[ $appended -eq 0 ]]; then
        printf '\n# Phase 19 SMTP bootstrap (configure_smtp_prod.sh)\n'
        appended=1
      fi
      printf '%s=%s\n' "$k" "${KV[$k]}"
    fi
  done
} >>"$TMP"
mv "$TMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "OK · /app/backend/.env updated (password not echoed)"

# 2. Restart the backend so all workers reload env.
sudo supervisorctl restart backend >/dev/null
sleep 4

# 3. Validate /status.
STATUS_JSON="$(curl -fsS -H "X-Admin-Token: $ADMIN_TOKEN" "$API_BASE/api/admin/email_relay/status")"
CONFIGURED="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['configured'])" "$STATUS_JSON")"
echo "configured: $CONFIGURED"
if [[ "$CONFIGURED" != "True" ]]; then
  echo "FAIL · /status reports configured=false. Aborting before test-send."
  exit 1
fi

# 4. Canary send against SALE-2026-0018 (the standing test fixture).
RESEND_JSON="$(curl -fsS -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
  "$API_BASE/api/admin/sales/SALE-2026-0018/resend_email" || echo '{"ok":false,"reason":"http_error"}')"
python3 - <<PYEOF
import json
d = json.loads("""$RESEND_JSON""")
print("test-send · ok:", d.get("ok"), "· reason:", d.get("reason"))
r = d.get("routing") or {}
print("test-send · TO:", r.get("to"), "· CC count:", len(r.get("cc") or []))
PYEOF

echo "Done. Delete this script now: rm -f $(realpath "$0")"
