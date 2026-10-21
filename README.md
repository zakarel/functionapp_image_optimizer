# Function App Image Optimizer

Azure Functions image-optimization service extracted from the TipRanks demo. The repository contains one application and two equivalent deployment paths:

1. **Terraform** provisions the Azure resources declaratively.
2. **Azure CLI** provisions the same resources through a Bash script.

Both paths create a Linux Elastic Premium Function App, Azure Container Registry, host storage, private image containers, managed identities, and least-privilege role assignments. No credentials are stored in the repository.

## What the function does

The Python v2 Function App runs ImageMagick in a hardened custom container. It accepts JPEG input, writes originals to the private `incoming` container, creates optimized variants in `optimized`, and authenticates to Blob Storage with its managed identity.

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET, HEAD | Liveness check |
| `/upload/{name}.jpg` | POST | Upload a JPEG and create the default WebP |
| `/io/{name}.jpg` | GET, HEAD | Generate or retrieve a cached transformed variant |
| `/optimized/{name}.webp` | GET, HEAD | Retrieve a default optimized image |

`/io` supports width, height, DPR, fit, crop, quality, output format, orientation, trim, grayscale, brightness, contrast, saturation, sharpen, blur, and background colour. The implementation caps upload size, output size, dimensions, pixels, query length, ImageMagick memory, disk, execution time, and enabled codecs.

## Prerequisites

- Azure CLI authenticated with permission to create resources and role assignments
- An Azure subscription ID
- Terraform 1.6+ for the Terraform path

Azure Container Registry builds the image remotely, so local Docker is not required.

## Option 1: Terraform

```bash
export AZURE_SUBSCRIPTION_ID="<subscription-id>"
./scripts/deploy-terraform.sh
```

Optional Terraform inputs can be placed in `infra/terraform/terraform.tfvars`; start from `terraform.tfvars.example`. The script performs a bootstrap apply, builds the application image in the new registry, and applies the immutable image tag to the Function App.

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

Reuse `RESOURCE_SUFFIX` when rerunning the CLI path against an existing deployment.

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

The deployed TipRanks environment also used VNet integration, private endpoints, private DNS, and Azure Front Door. Those shared demo components are intentionally excluded here; this repository is scoped to a standalone, reachable Function App and its direct runtime dependencies.
