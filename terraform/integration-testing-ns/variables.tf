locals {
  test_integration_image = var.test_integration_image == null ? "tobiashvmz/pxc-integration" : var.test_integration_image
  test_integration_version = var.test_integration_version == null ? "3.8.1" : var.test_integration_version
}

variable "test_integration_image" {
  type = string
  default = null
  description = "When set to non null value will use that insead of hardcoded image in locals."
}

variable "test_integration_version" {
  type = string
  default = null
  description = "Image version, normally hardcoded, only set in test cases."
}

variable "model_server_url" {
  description = "Base URL of the OpenAI-compatible model server"
  type        = string
}

variable "model_auth_header_value" {
  description = "Authorization header value for the model server"
  type        = string
  sensitive   = true
}

variable "model_name" {
  description = "Embedding model name"
  type        = string
}

variable "j2_query_template" {
  description = "Jinja2 query template to for the model server"
  type        = string
  default     = "Instruct: {{ task_description }}\nQuery:{{ query }}"
}
