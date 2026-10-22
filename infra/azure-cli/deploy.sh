#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-functionapp-image-optimizer}"
LOCATION="${LOCATION:-swedencentral}"
NAME_PREFIX="${NAME_PREFIX:-imageopt}"
IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d.%H%M%S)}"
SOURCE_PATH_PREFIX="${SOURCE_PATH_PREFIX:-}"
DEFAULT_WEBP_QUALITY="${DEFAULT_WEBP_QUALITY:-85}"

if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "Set AZURE_SUBSCRIPTION_ID before running this script." >&2
  exit 1
fi

command -v az >/dev/null || {
  echo "Missing required command: az" >&2
  exit 1
}

if [[ ! "$NAME_PREFIX" =~ ^[a-z][a-z0-9]{2,10}$ ]]; then
  echo "NAME_PREFIX must be 3-11 lowercase alphanumeric characters and start with a letter." >&2
  exit 1
fi

SUFFIX="${RESOURCE_SUFFIX:-$(printf '%s' "${USER:-user}$(date +%s)" | shasum | cut -c1-6)}"
BASE_NAME="${NAME_PREFIX}${SUFFIX}"
HOST_STORAGE="${BASE_NAME}host"
IMAGE_STORAGE="${BASE_NAME}img"
ACR_NAME="${BASE_NAME}acr"
PLAN_NAME="${NAME_PREFIX}-${SUFFIX}-plan"
FUNCTION_NAME="${NAME_PREFIX}-${SUFFIX}-fn"

az account set --subscription "$SUBSCRIPTION_ID"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --tags \
  application=functionapp-image-optimizer managed_by=azure-cli --output none

for storage_account in "$HOST_STORAGE" "$IMAGE_STORAGE"; do
  az storage account create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$storage_account" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --min-tls-version TLS1_2 \
    --allow-blob-public-access false \
    --https-only true \
    --output none
done

IMAGE_STORAGE_KEY="$(az storage account keys list \
  --resource-group "$RESOURCE_GROUP" \
  --account-name "$IMAGE_STORAGE" \
  --query '[0].value' --output tsv)"

for container in incoming optimized; do
  az storage container create \
    --account-name "$IMAGE_STORAGE" \
    --account-key "$IMAGE_STORAGE_KEY" \
    --name "$container" \
    --public-access off \
    --output none
done
unset IMAGE_STORAGE_KEY

az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --location "$LOCATION" \
  --sku Basic \
  --admin-enabled false \
  --output none

az functionapp plan create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$PLAN_NAME" \
  --location "$LOCATION" \
  --sku EP1 \
  --is-linux true \
  --output none

az functionapp create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FUNCTION_NAME" \
  --plan "$PLAN_NAME" \
  --storage-account "$HOST_STORAGE" \
  --functions-version 4 \
  --runtime custom \
  --image mcr.microsoft.com/azure-functions/python:4-python3.11 \
  --assign-identity \
  --https-only true \
  --output none

PRINCIPAL_ID="$(az functionapp identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FUNCTION_NAME" \
  --query principalId --output tsv)"
ACR_ID="$(az acr show --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --query id --output tsv)"
HOST_STORAGE_ID="$(az storage account show --resource-group "$RESOURCE_GROUP" --name "$HOST_STORAGE" --query id --output tsv)"
IMAGE_STORAGE_ID="$(az storage account show --resource-group "$RESOURCE_GROUP" --name "$IMAGE_STORAGE" --query id --output tsv)"

assign_role() {
  local scope="$1"
  local role="$2"
  az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --scope "$scope" \
    --role "$role" \
    --output none
}

assign_role "$ACR_ID" "AcrPull"
assign_role "$HOST_STORAGE_ID" "Storage Blob Data Owner"
assign_role "$HOST_STORAGE_ID" "Storage Queue Data Contributor"
assign_role "$HOST_STORAGE_ID" "Storage Table Data Contributor"
assign_role "$HOST_STORAGE_ID" "Storage Account Contributor"
assign_role "$IMAGE_STORAGE_ID" "Storage Blob Data Contributor"

az functionapp config appsettings set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FUNCTION_NAME" \
  --settings \
    FUNCTIONS_WORKER_RUNTIME=python \
    AzureWebJobsFeatureFlags=EnableWorkerIndexing \
    "AzureWebJobsStorage__accountName=$HOST_STORAGE" \
    "IMAGE_STORAGE_ACCOUNT=$IMAGE_STORAGE" \
    IMAGE_INPUT_CONTAINER=incoming \
    IMAGE_OUTPUT_CONTAINER=optimized \
    "SOURCE_PATH_PREFIX=$SOURCE_PATH_PREFIX" \
    "DEFAULT_WEBP_QUALITY=$DEFAULT_WEBP_QUALITY" \
    WEBSITES_ENABLE_APP_SERVICE_STORAGE=false \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
  --output none
az functionapp config appsettings delete \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FUNCTION_NAME" \
  --setting-names AzureWebJobsStorage \
  --output none

az acr build \
  --registry "$ACR_NAME" \
  --image "image-optimizer:$IMAGE_TAG" \
  "$ROOT_DIR/function_app"

LOGIN_SERVER="$(az acr show --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --query loginServer --output tsv)"
az resource update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FUNCTION_NAME" \
  --resource-type Microsoft.Web/sites \
  --set properties.siteConfig.acrUseManagedIdentityCreds=true \
  --output none
az functionapp config container set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FUNCTION_NAME" \
  --image "$LOGIN_SERVER/image-optimizer:$IMAGE_TAG" \
  --registry-server "https://$LOGIN_SERVER" \
  --output none
az functionapp restart --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_NAME"

echo "Deployment complete: https://$FUNCTION_NAME.azurewebsites.net"
echo "Health endpoint: https://$FUNCTION_NAME.azurewebsites.net/health"
