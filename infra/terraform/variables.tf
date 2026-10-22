variable "subscription_id" {
  description = "Azure subscription in which to deploy."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name."
  type        = string
  default     = "rg-functionapp-image-optimizer"
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "swedencentral"
}

variable "name_prefix" {
  description = "Lowercase prefix used for globally unique resource names."
  type        = string
  default     = "imageopt"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,10}$", var.name_prefix))
    error_message = "name_prefix must be 3-11 lowercase alphanumeric characters and start with a letter."
  }
}

variable "container_image_name" {
  description = "Repository name inside ACR."
  type        = string
  default     = "image-optimizer"
}

variable "container_image_tag" {
  description = "Image tag to deploy. Leave empty for the bootstrap Functions image."
  type        = string
  default     = ""
}

variable "source_path_prefix" {
  description = "Optional Blob prefix containing original WordPress images."
  type        = string
  default     = ""
}

variable "default_webp_quality" {
  description = "Default ImageMagick WebP quality for GET-on-demand generation."
  type        = number
  default     = 85

  validation {
    condition     = var.default_webp_quality >= 1 && var.default_webp_quality <= 100
    error_message = "default_webp_quality must be between 1 and 100."
  }
}
