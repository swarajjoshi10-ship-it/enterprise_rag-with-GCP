# --- SECRET MANAGER ---
# API keys and the DB password live in Secret Manager instead of plain Cloud Run
# env vars. Note: values still pass through terraform.tfstate, which stays local
# and gitignored.

locals {
  optional_secrets = {
    "logfire-token"     = nonsensitive(var.logfire_token != "")
    "langsmith-api-key" = nonsensitive(var.langsmith_api_key != "")
  }

  secret_names = concat(
    ["groq-api-key", "qdrant-api-key", "db-password"],
    [for name, enabled in local.optional_secrets : name if enabled]
  )

  secret_values = {
    "groq-api-key"      = var.groq_api_key
    "qdrant-api-key"    = var.qdrant_api_key
    "db-password"       = var.db_password
    "logfire-token"     = var.logfire_token
    "langsmith-api-key" = var.langsmith_api_key
  }

  # Which service account may read which secret
  secret_access = {
    backend   = ["groq-api-key", "qdrant-api-key", "db-password", "logfire-token", "langsmith-api-key"]
    ui        = ["logfire-token", "langsmith-api-key"]
    ingestion = ["qdrant-api-key", "logfire-token", "langsmith-api-key"]
  }

  service_account_emails = {
    backend   = google_service_account.backend_sa.email
    ui        = google_service_account.ui_sa.email
    ingestion = google_service_account.ingestion_sa.email
  }

  secret_bindings = {
    for pair in flatten([
      for sa, names in local.secret_access : [
        for name in names : { sa = sa, name = name } if contains(local.secret_names, name)
      ]
    ]) : "${pair.sa}-${pair.name}" => pair
  }

  # Env var name -> secret name, per service (only secrets that exist)
  backend_secret_env = { for k, v in {
    GROQ_API_KEY      = "groq-api-key"
    QDRANT_API_KEY    = "qdrant-api-key"
    DB_PASS           = "db-password"
    LOGFIRE_TOKEN     = "logfire-token"
    LANGSMITH_API_KEY = "langsmith-api-key"
  } : k => v if contains(local.secret_names, v) }

  ui_secret_env = { for k, v in {
    LOGFIRE_TOKEN     = "logfire-token"
    LANGSMITH_API_KEY = "langsmith-api-key"
  } : k => v if contains(local.secret_names, v) }

  ingestion_secret_env = { for k, v in {
    QDRANT_API_KEY    = "qdrant-api-key"
    LOGFIRE_TOKEN     = "logfire-token"
    LANGSMITH_API_KEY = "langsmith-api-key"
  } : k => v if contains(local.secret_names, v) }

  langsmith_tracing = nonsensitive(var.langsmith_api_key != "") ? "true" : "false"
}

resource "google_secret_manager_secret" "app" {
  for_each  = toset(local.secret_names)
  secret_id = "rag-${each.key}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "app" {
  for_each    = google_secret_manager_secret.app
  secret      = each.value.id
  secret_data = local.secret_values[each.key]
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = local.secret_bindings
  secret_id = google_secret_manager_secret.app[each.value.name].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.service_account_emails[each.value.sa]}"
}
