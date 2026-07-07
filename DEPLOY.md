# Deploying Dark Ships

Same infrastructure as prod-battle: a shared **Scaleway Kapsule** cluster
(ingress-nginx + cert-manager + Prometheus/Loki/Grafana), images on **ghcr.io**,
managed **Postgres 16**, **Wasabi** object storage, **Cloudflare** DNS.

- **Backend** (this repo, `backend/`) → k8s Deployments in the `dark-ships`
  namespace (`k8s/`), rolled by `.github/workflows/deploy.yml`.
- **Frontend** (`frontend/`) → Cloudflare Workers (`wrangler.jsonc`), served at
  `darkships.org`.

The backend runs as **two Deployments** from one image:
- `web` — `RUN_MODE=web`, 2+ replicas, behind the Ingress. Scales freely.
- `worker` — `RUN_MODE=worker`, **exactly 1 replica** (`strategy: Recreate`).
  Owns the AIS WebSocket, all scheduled jobs, and DB schema/partition setup.

## One-time setup

1. **Wasabi bucket** for the cold tier (same account as prod-battle):
   ```bash
   aws --endpoint-url https://s3.eu-central-2.wasabisys.com s3 mb s3://dark-ships-cold
   ```

2. **Postgres 16** — a database on the shared managed instance (or a new one).
   Build the `DATABASE_URL` (`postgresql+asyncpg://…?sslmode=require`).

3. **Cloudflare DNS** (token `CLOUDFLARE_API_TOKEN`, DNS-write on darkships.org):
   - `api.darkships.org` → the Kapsule node IP, **DNS-only (grey cloud)** so
     cert-manager's HTTP-01 challenge reaches nginx.
     (node IP: `tofu -chdir=envs/prod output -json k8s_node_ips` in the infra repo.)
   - `darkships.org` + `www` → the Cloudflare Worker (added as a custom domain
     on the `dark-ships` Worker in the dashboard).

4. **GitHub secrets**
   | Scope | Name | Value |
   |---|---|---|
   | Environment `production` | `KUBECONFIG` | `tofu -chdir=envs/prod output -raw kubeconfig` (infra repo) |

   (Image push uses the built-in `GITHUB_TOKEN` — no registry secret.)

5. **Cluster Secret** `ds-secrets` (kubectl only, never in git — see
   `k8s/secrets.example.yaml`):
   ```bash
   kubectl -n dark-ships create namespace dark-ships 2>/dev/null || true
   kubectl -n dark-ships create secret generic ds-secrets \
     --from-literal=DATABASE_URL='postgresql+asyncpg://USER:PASS@HOST:5432/darkships?sslmode=require' \
     --from-literal=AUTH_SECRET="$(openssl rand -hex 32)" \
     --from-literal=AISSTREAM_API_KEY_REGIONS='<key>' \
     --from-literal=GFW_API_TOKEN='<token>' \
     --from-literal=S3_ACCESS_KEY_ID='<wasabi-key>' \
     --from-literal=S3_SECRET_ACCESS_KEY='<wasabi-secret>' \
     --from-literal=AIS_REGIONS='[{"name":"...","bbox":[[..]]}]' \
     --from-literal=GOOGLE_OAUTH_CLIENT_ID='' \
     --from-literal=GOOGLE_OAUTH_CLIENT_SECRET=''
   ```

6. **Frontend build var** (Cloudflare Workers → Settings → Variables):
   `VITE_API_BASE_URL=https://api.darkships.org`.

## Deploy

Every push to `main` runs `deploy.yml`: build image → push ghcr → `kubectl
apply -k k8s/` → roll `worker` (runs migrations) then `web` → smoke-test
`https://api.darkships.org/api/health` for the new SHA.

Manual roll / rollback to any built SHA:
```bash
kubectl -n dark-ships set image deployment/web web=ghcr.io/brammittendorff/dark-ships:<sha>
kubectl -n dark-ships set image deployment/worker worker=ghcr.io/brammittendorff/dark-ships:<sha>
```

## Observability

Reuses the shared Grafana/Loki/Prometheus. Logs:
`{namespace="dark-ships"}`. The worker logs `Starting in WORKER mode`; the web
pods log `Starting in WEB mode`.
