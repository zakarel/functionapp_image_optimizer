FROM mcr.microsoft.com/azure-functions/python:4-python3.11

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
    FUNCTIONS_EXTENSION_VERSION=~4 \
    FUNCTIONS_WORKER_RUNTIME=python \
    FUNCTIONS_WORKER_RUNTIME_VERSION=3.11 \
    AzureWebJobsFeatureFlags=EnableWorkerIndexing \
    PYTHONPATH=/home/site/wwwroot/.python_packages/lib/site-packages

RUN apt-get update \
    && apt-get install --no-install-recommends -y imagemagick \
    && rm -rf /var/lib/apt/lists/*

COPY imagemagick-policy.xml /etc/ImageMagick-6/policy.xml
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir \
        --target=/home/site/wwwroot/.python_packages/lib/site-packages \
        -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && convert -list format | grep -q 'WEBP'

COPY host.json function_app.py /home/site/wwwroot/
