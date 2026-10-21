#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/infra/terraform"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d.%H%M%S)}"

if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "Set AZURE_SUBSCRIPTION_ID before running this script." >&2
  exit 1
fi

for command in az terraform jq; do
  command -v "$command" >/dev/null || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done

az account set --subscription "$SUBSCRIPTION_ID"
terraform -chdir="$TF_DIR" init
terraform -chdir="$TF_DIR" apply -auto-approve \
  -var "subscription_id=$SUBSCRIPTION_ID"

ACR_NAME="$(terraform -chdir="$TF_DIR" output -raw container_registry_name)"
az acr build \
  --registry "$ACR_NAME" \
  --image "image-optimizer:$IMAGE_TAG" \
  "$ROOT_DIR/function_app"

terraform -chdir="$TF_DIR" apply -auto-approve \
  -var "subscription_id=$SUBSCRIPTION_ID" \
  -var "container_image_tag=$IMAGE_TAG"

FUNCTION_URL="$(terraform -chdir="$TF_DIR" output -raw function_app_url)"
echo "Deployment complete: $FUNCTION_URL"
echo "Health endpoint: $FUNCTION_URL/health"

