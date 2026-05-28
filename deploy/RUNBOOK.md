# Mackertich ONE — Production Ops Runbook (Phase 28)

Production is hosted on a **customer-owned VPS** (Hostinger). Emergent preview
is the staging environment; promotions to production happen via this runbook.

## Topology

```
Internet → DNS bot.pesmifs.com (grey-cloud, Cloudflare DNS-only)
        → 187.127.174.187 (Ubuntu 24.04, 2 vCPU, 7.8 GB RAM, 96 GB disk)
           ├─ nginx :80→:443 (Let's Encrypt cert, auto-renew via certbot.timer)
           │   ├─ /                 → /opt/mackertich/frontend-dist/    (CRA static)
           │   └─ /api/*            → 127.0.0.1:8001                    (uvicorn)
           ├─ docker compose stack (in /opt/mackertich/)
           │   ├─ mackertich-backend (Python 3.12-slim, FastAPI/uvicorn)
           │   └─ mackertich-mongo   (mongo:7, 1 GB WiredTiger cache, localhost-bound)
           └─ systemd timer: mackertich-mongodump.timer (03:00 UTC daily)
```

## SSH

```bash
# From preview pod (key already present):
ssh vps                # uses /root/.ssh/mackertich_deploy_ed25519

# Direct equivalent:
ssh -i /root/.ssh/mackertich_deploy_ed25519 root@187.127.174.187
```

**Password auth is DISABLED** on the VPS. Pubkey only.

## Key paths (on VPS)

| Path | Purpose |
|---|---|
| `/opt/mackertich/.env` | Secrets (chmod 600 root). Never commit. |
| `/opt/mackertich/docker-compose.yml` | Service definitions. |
| `/opt/mackertich/.compose-env` | Pins `BACKEND_TAG=phase28-260528-1`. |
| `/opt/mackertich/source-backend/` | Backend source tree + Dockerfile (rsync mirror). |
| `/opt/mackertich/frontend-dist/` | CRA `build/` output. Served by nginx. |
| `/opt/mackertich/mongo-data/` | MongoDB persistence (bind-mounted). |
| `/etc/nginx/sites-enabled/mackertich` | nginx vhost (managed by certbot). |
| `/etc/letsencrypt/live/bot.pesmifs.com/` | TLS cert + key. |
| `/var/log/mackertich/deploy-*.log` | Full deploy transcripts. |
| `/var/log/mackertich/mongodump.log` | Nightly backup log. |
| `/var/log/mackertich/health.log` | Cron uptime monitor (only writes when prod is down). |
| `/var/backups/mongodump/dump-YYYYMMDD.gz` | Nightly backups (14-day retention). |
| `/var/backups/mongodump/initial-migration-20260528.gz` | The preview→VPS migration snapshot. |

## Common operations

### Restart backend
```bash
ssh vps 'cd /opt/mackertich && docker compose restart backend'
```

### Restart everything
```bash
ssh vps 'cd /opt/mackertich && docker compose down && docker compose --env-file .compose-env up -d'
```

### Tail logs
```bash
ssh vps 'cd /opt/mackertich && docker compose logs -f --tail=200 backend'
ssh vps 'cd /opt/mackertich && docker compose logs -f --tail=200 mongo'
ssh vps 'tail -f /var/log/nginx/access.log /var/log/nginx/error.log'
```

### Deploy a new backend image
1. Rsync updated `backend/` source from preview to VPS:
   ```bash
   rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='.env' \
     -e "ssh -i /root/.ssh/mackertich_deploy_ed25519" \
     /app/backend/ vps:/opt/mackertich/source-backend/
   ```
2. Build a new tag on the VPS:
   ```bash
   ssh vps 'cd /opt/mackertich/source-backend && \
            docker build -t mackertich-backend:phase29-$(date +%y%m%d)-1 \
                         -t mackertich-backend:latest .'
   ```
3. Pin the new tag and roll forward:
   ```bash
   ssh vps 'echo "BACKEND_TAG=phase29-260601-1" > /opt/mackertich/.compose-env && \
            cd /opt/mackertich && docker compose --env-file .compose-env up -d backend'
   ```

### Deploy a new frontend build
```bash
# In preview:
cd /app/frontend && REACT_APP_BACKEND_URL="" GENERATE_SOURCEMAP=false DISABLE_ESLINT_PLUGIN=true yarn build
rsync -a --delete \
  -e "ssh -i /root/.ssh/mackertich_deploy_ed25519" \
  build/ vps:/opt/mackertich/frontend-dist/
# No reload needed — nginx serves directly from disk.
```

### Rollback (image-pinned)
Docker keeps prior tags. To roll back the backend:
```bash
ssh vps 'docker images mackertich-backend'   # list available tags
ssh vps 'echo "BACKEND_TAG=<prior-tag>" > /opt/mackertich/.compose-env && \
         cd /opt/mackertich && docker compose --env-file .compose-env up -d backend'
```

Frontend rollback: keep a `frontend-dist-<tag>/` snapshot before each deploy.

### Backup + restore
```bash
# Trigger an on-demand backup:
ssh vps 'systemctl start mackertich-mongodump.service'

# Restore from a specific snapshot:
ssh vps 'cat /var/backups/mongodump/dump-20260528.gz | \
         docker exec -i mackertich-mongo mongorestore --gzip --archive --drop'
```

## Monitoring

- **Uptime**: cron pings `https://bot.pesmifs.com/api/health` every 5 min;
  failures append to `/var/log/mackertich/health.log`. If the file grows,
  something's wrong.
- **Cert renewal**: `systemctl list-timers certbot.timer` → fires twice daily.
- **Backup freshness**: `systemctl list-timers mackertich-mongodump.timer` →
  03:00 UTC daily. `ls -lh /var/backups/mongodump/` should always show today.
- **fail2ban**: `fail2ban-client status sshd` — bans for 10 min after 5 failed
  attempts.

## Admin endpoints (`Authorization: Bearer smifs-admin-2026`)

```bash
curl -sS -H "Authorization: Bearer smifs-admin-2026" \
  https://bot.pesmifs.com/api/admin/errors/recent?limit=10
curl -sS -H "Authorization: Bearer smifs-admin-2026" \
  https://bot.pesmifs.com/api/admin/errors/summary
curl -sS -H "Authorization: Bearer smifs-admin-2026" \
  https://bot.pesmifs.com/api/admin/reembed/estimate
curl -sS -H "Authorization: Bearer smifs-admin-2026" \
  https://bot.pesmifs.com/api/admin/forms/submissions?limit=20
curl -sS -H "Authorization: Bearer smifs-admin-2026" \
  https://bot.pesmifs.com/api/admin/insight/top_asks?days=7
```

## Outage playbook

1. **Cert expired** (>90d, certbot.timer dead): `ssh vps 'certbot renew'`.
2. **Mongo OOM**: check `docker stats mackertich-mongo`. Cache is capped at
   1 GB; if RSS climbs above that, restart: `docker compose restart mongo`.
   Investigate large collections via mongosh.
3. **Backend crash loop**: `docker compose logs --tail=200 backend`. Most
   likely a missing env var or a Hub AI outage. Health endpoint shows
   `llm_reachable: false` when Hub AI is down.
4. **Hot standby**: Emergent preview at https://wealth-chat-4.preview.emergentagent.com/
   remains permanently alive. If prod is broken and you can't recover,
   temporarily point DNS back at Cloudflare/preview.

## Phase 28 deploy artifacts

- Initial migration: 14,206 documents across 37 collections restored from
  `test_database` (preview) → `mackertich_prod` (VPS) on 2026-05-28.
- Initial backend image: `mackertich-backend:phase28-260528-1` (3.22 GB).
- Initial frontend build: CRA with `REACT_APP_BACKEND_URL=""` (same-origin).
- Phase 27 metadata backfill: 2022/2022 doc_chunks marked
  `embedding_model=text-embedding-3-large, embedding_dim=3072` (vectors were
  already 3072-dim from preview's CLI re-embed; only metadata was missing).
