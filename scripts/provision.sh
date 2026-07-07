#!/usr/bin/env bash
# One-shot provisioning for Dark Ships on the shared prod-battle infra.
# Run LOCALLY with your infra credentials sourced - it reads secrets from the
# environment and never prints or stores them. Nothing here is committed with
# real values.
#
#   cd ~/work/dark-ships
#   set -a; source ../producer-battle/prod-battle-infra/.env; set +a
#   # app-specific values (not in the infra .env):
#   export AISSTREAM_API_KEY_REGIONS=...   GFW_API_TOKEN=...
#   export DATABASE_URL='postgresql+asyncpg://USER:PASS@HOST:5432/darkships?sslmode=require'
#   export AIS_REGIONS='[{"name":"...","bbox":[[..]]}]'
#   bash scripts/provision.sh
#
# Assumes: the Wasabi app keys are in S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY
# (or TF_VAR_s3_access_key/TF_VAR_s3_secret_key), CLOUDFLARE_API_TOKEN is set,
# kubectl is pointed at the Kapsule cluster, and `gh` is authed as brammittendorff.
set -euo pipefail

INFRA_DIR="${INFRA_DIR:-../producer-battle/prod-battle-infra}"
WASABI_ENDPOINT="https://s3.eu-central-2.wasabisys.com"
BUCKET="dark-ships-cold"
DOMAIN="darkships.org"
API_HOST="api.darkships.org"

# accept either naming for the Wasabi app keys
S3_KEY="${S3_ACCESS_KEY_ID:-${TF_VAR_s3_access_key:-}}"
S3_SECRET="${S3_SECRET_ACCESS_KEY:-${TF_VAR_s3_secret_key:-}}"

need() { [ -n "${!1:-}" ] || { echo "MISSING env: $1"; MISS=1; }; }
echo "== preflight =="
MISS=0
need CLOUDFLARE_API_TOKEN
need DATABASE_URL
[ -n "$S3_KEY" ] && [ -n "$S3_SECRET" ] || { echo "MISSING: Wasabi app keys (S3_ACCESS_KEY_ID/SECRET or TF_VAR_s3_*)"; MISS=1; }
[ "$MISS" = 0 ] || { echo "Fill the vars above and re-run."; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl not found / not configured"; exit 1; }

echo "== 1. Wasabi cold-tier bucket =="
AWS_ACCESS_KEY_ID="$S3_KEY" AWS_SECRET_ACCESS_KEY="$S3_SECRET" \
  aws --endpoint-url "$WASABI_ENDPOINT" s3 mb "s3://$BUCKET" 2>/dev/null \
  && echo "created $BUCKET" || echo "$BUCKET already exists (ok)"

echo "== 2. cluster node IP (for the api DNS record) =="
NODE_IP="${NODE_IP:-$(tofu -chdir="$INFRA_DIR/envs/prod" output -json k8s_node_ips 2>/dev/null | jq -r '.[0]' || true)}"
[ -n "$NODE_IP" ] && [ "$NODE_IP" != "null" ] || { echo "Could not read node IP - set NODE_IP=x.x.x.x and re-run"; exit 1; }
echo "node IP: $NODE_IP"

echo "== 3. DNS: $API_HOST -> $NODE_IP (grey-cloud, DNS-only) =="
ZONE_ID=$(curl -fsS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" | jq -r '.result[0].id')
REC_ID=$(curl -fsS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=A&name=$API_HOST" | jq -r '.result[0].id // empty')
BODY=$(jq -nc --arg ip "$NODE_IP" '{type:"A",name:"api",content:$ip,proxied:false,ttl:300}')
if [ -n "$REC_ID" ]; then
  curl -fsS -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$REC_ID" \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" --data "$BODY" >/dev/null
  echo "updated existing A record"
else
  curl -fsS -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
    -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" --data "$BODY" >/dev/null
  echo "created A record"
fi

echo "== 4. k8s Secret ds-secrets =="
kubectl create namespace dark-ships --dry-run=client -o yaml | kubectl apply -f -
kubectl -n dark-ships create secret generic ds-secrets \
  --from-literal=DATABASE_URL="$DATABASE_URL" \
  --from-literal=AUTH_SECRET="${AUTH_SECRET:-$(openssl rand -hex 32)}" \
  --from-literal=AISSTREAM_API_KEY_REGIONS="${AISSTREAM_API_KEY_REGIONS:-}" \
  --from-literal=GFW_API_TOKEN="${GFW_API_TOKEN:-}" \
  --from-literal=S3_ACCESS_KEY_ID="$S3_KEY" \
  --from-literal=S3_SECRET_ACCESS_KEY="$S3_SECRET" \
  --from-literal=AIS_REGIONS="${AIS_REGIONS:-[]}" \
  --from-literal=GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-}" \
  --from-literal=GOOGLE_OAUTH_CLIENT_SECRET="${GOOGLE_OAUTH_CLIENT_SECRET:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "== 5. apply manifests =="
kubectl apply -k k8s/

echo "== 6. GitHub env secret KUBECONFIG (for CI rollouts) =="
if command -v gh >/dev/null; then
  tofu -chdir="$INFRA_DIR/envs/prod" output -raw kubeconfig 2>/dev/null | base64 -w0 \
    | gh secret set KUBECONFIG --env production --repo brammittendorff/dark-ships 2>/dev/null \
    && echo "set KUBECONFIG on the production environment" \
    || echo "skip: set KUBECONFIG manually (gh env 'production' may need creating first)"
fi

echo ""
echo "DONE. Remaining manual bits:"
echo " - Postgres 16 DB must already exist (DATABASE_URL points at it)."
echo " - Cloudflare Worker (frontend): add darkships.org + www as custom domains,"
echo "   and set build var VITE_API_BASE_URL=https://$API_HOST."
echo " - Push to main to trigger the build+rollout, then: curl https://$API_HOST/api/health"
