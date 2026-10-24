variable "pxc_integration_registry" {
  type = string
}

variable "test_integration_version" {
  type = string
  default = "pxc-3-stable"
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
