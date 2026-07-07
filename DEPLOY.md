# Deploying Dark Ships

Runs on the **same** infrastructure as prod-battle: the shared Scaleway Kapsule
cluster, ghcr.io images, managed Postgres 16, Wasabi, Cloudflare, all via
OpenTofu in the **`prod-battle-infra`** repo.

- **Infra** (`prod-battle-infra/modules/dark-ships`, wired in `envs/prod`):
  owns the `dark-ships` namespace, `web` + `worker` Deployments, Service,
  Ingress (`api.darkships.org`), ConfigMap, and the DNS record. Applied with
  `tofu`.
- **App** (this repo): `backend/` image + `.github/workflows/deploy.yml` which
  builds → pushes ghcr → `kubectl set image` on the two Deployments. No k8s
  manifests live here.
- **Frontend** (`frontend/`): Cloudflare Worker (`wrangler.jsonc`) at
  `darkships.org`.

The backend is two Deployments from one image: `web` (`RUN_MODE=web`, scales)
and `worker` (`RUN_MODE=worker`, **1 replica, Recreate** — the AIS ingester +
scheduler + migrations).

## One-time setup

In **`prod-battle-infra`**:
1. Create the cold-tier Wasabi bucket (out-of-band, no TF provider):
   `aws --endpoint-url https://s3.eu-central-2.wasabisys.com s3 mb s3://dark-ships-cold`
2. Add the GH secret `TF_VAR_darkships_zone_id` = Cloudflare zone ID for
   `darkships.org` (same CF token, it already covers both zones).
3. `tofu -chdir=envs/prod apply` — creates the namespace, Deployments, Ingress,
   and the `api.darkships.org` A record.

In the cluster (kubectl, secrets never in git or TF):
4. Create `ds-secrets`:
   ```bash
   kubectl -n dark-ships create secret generic ds-secrets \
     --from-literal=DATABASE_URL='postgresql+asyncpg://USER:PASS@HOST:5432/darkships?sslmode=require' \
     --from-literal=AUTH_SECRET="$(openssl rand -hex 32)" \
     --from-literal=AISSTREAM_API_KEY_REGIONS='<key>' \
     --from-literal=GFW_API_TOKEN='<token>' \
     --from-literal=S3_ACCESS_KEY_ID='<wasabi-key>' \
     --from-literal=S3_SECRET_ACCESS_KEY='<wasabi-secret>' \
     --from-literal=AIS_REGIONS='[{"name":"...","bbox":[[..]]}]'
   ```

In **this repo**:
5. GH environment `production` secret `KUBECONFIG` =
   `tofu -chdir=envs/prod output -raw kubeconfig` (infra repo).
6. Cloudflare Worker (frontend): add `darkships.org` + `www` as custom domains
   and set build var `VITE_API_BASE_URL=https://api.darkships.org`.

## Deploy / rollback

Push to `main` → build → ghcr → roll `worker` (runs migrations) then `web` →
smoke-test `https://api.darkships.org/api/health`. Roll to any SHA:
```bash
kubectl -n dark-ships set image deployment/worker worker=ghcr.io/brammittendorff/dark-ships:<sha>
kubectl -n dark-ships set image deployment/web    web=ghcr.io/brammittendorff/dark-ships:<sha>
```

Logs in the shared Grafana/Loki: `{namespace="dark-ships"}`.
