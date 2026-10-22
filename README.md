# Function App Image Optimizer

This repository contains the image optimization Function App built for the TipRanks demo. You can deploy the same application in either of two ways:

1. **Terraform** defines and provisions the Azure resources.
2. **Azure CLI** provisions them with a Bash script.

Both options create a Linux Elastic Premium Function App, an Azure Container Registry, host storage, private image containers, managed identities, and least-privilege role assignments. The repository does not store credentials.

## What the function does

The app uses the Python v2 programming model and runs ImageMagick in a hardened custom container. WordPress or another uploader writes original JPEG and PNG files to the private `incoming` container. The Function does not optimize during upload. When Front Door receives an image request that is not already cached, it calls the Function, which reads the source, generates a WebP on demand, stores it in `optimized`, and returns it. The Function accesses Blob Storage through its managed identity.

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET, HEAD | Liveness check |
| `/io/{source-path}` | GET, HEAD | Generate or retrieve a cached transformed variant for a JPEG or PNG source |

The `/io` route supports nested source paths, such as `/io/2026/10/photo.jpg` and `/io/2026/10/chart.png`. It supports width, height, DPR, fit, crop, quality, output format, orientation, trim, grayscale, brightness, contrast, saturation, sharpening, blur, and background colour. Limits are set for upload and output size, dimensions, pixel count, query length, ImageMagick memory, disk use, execution time, and enabled codecs.

## Front Door flow

The public webpage URL and Front Door route are customer-specific. Configure them with the placeholders below:

- `<PUBLIC_IMAGE_DOMAIN>` is the Front Door custom domain.
- `<PUBLIC_IMAGE_PATH_PATTERN>` is the public JPEG/PNG/WebP path pattern.
- `<AFD_ROUTE_PATTERN>` is the Front Door route for image requests.
- `<AFD_REWRITE_DESTINATION>` maps the public source path to `/io/{source-path}`.
- `<FUNCTION_ORIGIN_HOST>` is the Function App origin hostname.
- `<SOURCE_STORAGE_ACCOUNT>` and `<SOURCE_CONTAINER>` hold the originals.
- `<OPTIMIZED_CONTAINER>` holds generated variants.
- `<SOURCE_PATH_PREFIX>` is an optional prefix such as `wordpress/uploads`.

The intended request sequence is:

```text
Browser requests an image
  -> Front Door edge cache hit: return cached WebP
  -> Front Door miss: rewrite to the Function origin
  -> Function checks the deterministic variant in Blob Storage
  -> variant hit: return WebP
  -> variant miss: read JPEG/PNG, run ImageMagick, save WebP, return it
  -> Front Door caches the response
```

The visitor sees a normal webpage image request. The Function route and first-request conversion are implementation details.

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
export SOURCE_PATH_PREFIX="wordpress/uploads"
export DEFAULT_WEBP_QUALITY="85"
```

When rerunning the CLI deployment against existing resources, use the same `RESOURCE_SUFFIX`.

## Try it

```bash
FUNCTION_URL="https://<function-app-name>.azurewebsites.net"
curl --fail "$FUNCTION_URL/health"
curl --fail \
  "$FUNCTION_URL/io/2026/10/sample.jpg?width=640&height=360&fit=cover&format=webp&quality=82" \
  --output sample-optimized.webp
```

Upload originals directly to the configured private source container. The Function App no longer exposes an upload endpoint and never performs conversion during upload.

## Local tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r function_app/requirements.txt
PYTHONPATH=function_app python -m unittest discover -s tests
```

The TipRanks demo also used VNet integration, private endpoints, private DNS, and Azure Front Door. Those shared components are outside the scope of this repository, which deploys a standalone, reachable Function App with its direct runtime dependencies.
