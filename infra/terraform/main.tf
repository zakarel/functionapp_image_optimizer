resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

locals {
  base_name     = "${var.name_prefix}${random_string.suffix.result}"
  custom_image  = var.container_image_tag != ""
  registry_url  = local.custom_image ? "https://${azurerm_container_registry.main.login_server}" : "https://mcr.microsoft.com"
  image_name    = local.custom_image ? var.container_image_name : "azure-functions/python"
  image_tag     = local.custom_image ? var.container_image_tag : "4-python3.11"
  function_name = "${var.name_prefix}-${random_string.suffix.result}-fn"
  plan_name     = "${var.name_prefix}-${random_string.suffix.result}-plan"
  common_tags = {
    application = "functionapp-image-optimizer"
    managed_by  = "terraform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

resource "azurerm_storage_account" "host" {
  name                            = "${local.base_name}host"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.common_tags
}

resource "azurerm_storage_account" "images" {
  name                            = "${local.base_name}img"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.common_tags
}

resource "azurerm_storage_container" "incoming" {
  name                  = "incoming"
  storage_account_id    = azurerm_storage_account.images.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "optimized" {
  name                  = "optimized"
  storage_account_id    = azurerm_storage_account.images.id
  container_access_type = "private"
}

resource "azurerm_container_registry" "main" {
  name                = "${local.base_name}acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.common_tags
}

resource "azurerm_service_plan" "main" {
  name                = local.plan_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "EP1"
  tags                = local.common_tags
}

resource "azurerm_linux_function_app" "main" {
  name                          = local.function_name
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  service_plan_id               = azurerm_service_plan.main.id
  storage_account_name          = azurerm_storage_account.host.name
  storage_uses_managed_identity = true
  functions_extension_version   = "~4"
  https_only                    = true
  public_network_access_enabled = true
  tags                          = local.common_tags

  identity {
    type = "SystemAssigned"
  }

  app_settings = {
    FUNCTIONS_WORKER_RUNTIME                           = "python"
    AzureWebJobsFeatureFlags                           = "EnableWorkerIndexing"
    AzureWebJobsStorage__accountName                   = azurerm_storage_account.host.name
    IMAGE_STORAGE_ACCOUNT                              = azurerm_storage_account.images.name
    IMAGE_INPUT_CONTAINER                              = azurerm_storage_container.incoming.name
    IMAGE_OUTPUT_CONTAINER                             = azurerm_storage_container.optimized.name
    SOURCE_PATH_PREFIX                                 = var.source_path_prefix
    DEFAULT_WEBP_QUALITY                               = tostring(var.default_webp_quality)
    WEBSITES_ENABLE_APP_SERVICE_STORAGE                = "false"
    AzureFunctionsJobHost__Logging__Console__IsEnabled = "true"
  }

  site_config {
    always_on                               = true
    ftps_state                              = "Disabled"
    minimum_tls_version                     = "1.2"
    container_registry_use_managed_identity = local.custom_image

    application_stack {
      docker {
        registry_url = local.registry_url
        image_name   = local.image_name
        image_tag    = local.image_tag
      }
    }
  }
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "host_blob_owner" {
  scope                = azurerm_storage_account.host.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "host_queue_contributor" {
  scope                = azurerm_storage_account.host.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "host_table_contributor" {
  scope                = azurerm_storage_account.host.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "host_account_contributor" {
  scope                = azurerm_storage_account.host.id
  role_definition_name = "Storage Account Contributor"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "image_blob_contributor" {
  scope                = azurerm_storage_account.images.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}
