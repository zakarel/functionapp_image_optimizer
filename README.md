# Function App Image Optimizer

This repository contains the image optimization Function App built for the TipRanks demo. You can deploy the same application in either of two ways:

1. **Terraform** defines and provisions the Azure resources.
2. **Azure CLI** provisions them with a Bash script.

Both options create a Linux Elastic Premium Function App, an Azure Container Registry, host storage, private image containers, managed identities, and least-privilege role assignments. The repository does not store credentials.

## What the function does

The app uses the Python v2 programming model and runs ImageMagick in a hardened custom container. It accepts JPEG files, stores the originals in the private `incoming` container, and writes optimized versions to `optimized`. It accesses Blob Storage through its managed identity.

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET, HEAD | Liveness check |
| `/upload/{name}.jpg` | POST | Upload a JPEG and create the default WebP |
| `/io/{name}.jpg` | GET, HEAD | Generate or retrieve a cached transformed variant |
| `/optimized/{name}.webp` | GET, HEAD | Retrieve a default optimized image |

The `/io` route supports width, height, DPR, fit, crop, quality, output format, orientation, trim, grayscale, brightness, contrast, saturation, sharpening, blur, and background colour. Limits are set for upload and output size, dimensions, pixel count, query length, ImageMagick memory, disk use, execution time, and enabled codecs.

## Prerequisites

- Azure CLI authenticated with permission to create resources and role assignments
- An Azure subscription ID
- Terraform 1.6+ for the Terraform path

Azure Container Registry builds the image remotely. You do not need Docker installed locally.

## Option 1: Terraform

```bash
export AZURE_SUBSCRIPTION_ID="<subscription-id>"
./scripts/deploy-terraform.sh
```

For custom Terraform values, copy `terraform.tfvars.example` to `infra/terraform/terraform.tfvars` and edit it. The deployment script first creates the required Azure resources, then builds the application image in the new registry and configures the Function App to use its immutable image tag.

## Option 2: Azure CLI

```bash
export AZURE_SUBSCRIPTION_ID="<subscription-id>"
./infra/azure-cli/deploy.sh
```

Optional environment variables:

```bash
export RESOURCE_GROUP="rg-functionapp-image-optimizer"
export LOCATION="swedencentral"
export NAME_PREFIX="imageopt"
export RESOURCE_SUFFIX="a1b2c3"
```

When rerunning the CLI deployment against existing resources, use the same `RESOURCE_SUFFIX`.

## Try it

```bash
FUNCTION_URL="https://<function-app-name>.azurewebsites.net"
curl --fail "$FUNCTION_URL/health"
curl --fail \
  --request POST \
  --header "Content-Type: image/jpeg" \
  --data-binary @sample.jpg \
  "$FUNCTION_URL/upload/sample.jpg"
curl --fail \
  "$FUNCTION_URL/io/sample.jpg?width=640&height=360&fit=cover&format=webp&quality=82" \
  --output sample-optimized.webp
```

## Local tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r function_app/requirements.txt
PYTHONPATH=function_app python -m unittest discover -s tests
```

The TipRanks demo also used VNet integration, private endpoints, private DNS, and Azure Front Door. Those shared components are outside the scope of this repository, which deploys a standalone, reachable Function App with its direct runtime dependencies.
