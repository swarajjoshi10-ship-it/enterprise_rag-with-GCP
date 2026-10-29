variable "project_id" {
  description = "The GCP Project ID"
  type        = string
}

variable "region" {
  description = "The GCP region for services"
  type        = string
  default     = "us-central1"
}

variable "app_name" {
  description = "Base name for all resources"
  type        = string
  default     = "enterprise-rag"
}

# --- COST CONTROLS ---

variable "enable_redis" {
  description = "Create Memorystore Redis 7.2 for the semantic cache (~$36/month while it exists)"
  type        = bool
  default     = false
}

variable "max_instances" {
  description = "Max Cloud Run instances per public service (caps runaway LLM/Vertex usage)"
  type        = number
  default     = 2
}

variable "pdf_parser" {
  description = "PDF engine for ingestion: pypdf (free) or docai (Document AI OCR, ~$1.50 per 1,000 pages)"
  type        = string
  default     = "pypdf"

  validation {
    condition     = contains(["pypdf", "docai"], var.pdf_parser)
    error_message = "pdf_parser must be \"pypdf\" or \"docai\"."
  }
}

# --- EXTERNAL SERVICES ---

variable "qdrant_url" {
  description = "Qdrant Cloud endpoint"
  type        = string
}

variable "qdrant_api_key" {
  description = "Qdrant Cloud API Key"
  type        = string
  sensitive   = true
}

variable "groq_api_key" {
  description = "Groq API Key"
  type        = string
  sensitive   = true
}

variable "groq_model" {
  description = "Groq chat model used by the planner and responder"
  type        = string
  default     = "openai/gpt-oss-120b"
}

variable "db_password" {
  description = "Postgres Password"
  type        = string
  sensitive   = true
}

# --- OBSERVABILITY (optional: leave empty to disable) ---

variable "logfire_token" {
  description = "Logfire Write Token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "langsmith_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "langsmith_project" {
  type    = string
  default = "enterprise_rag"
}
