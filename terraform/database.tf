# --- CLOUD SQL (POSTGRES) ---

resource "google_sql_database_instance" "postgres" {
  name             = "${var.app_name}-db"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier    = "db-f1-micro" # Smallest tier to keep costs low during dev
    edition = "ENTERPRISE"

    ip_configuration {
      # Public IP with NO authorized networks: only the Cloud SQL connector
      # (used by Cloud Run's /cloudsql socket, IAM-authorized) can reach it
      ipv4_enabled = true
    }
  }
  deletion_protection = false # Set to true for production!

  depends_on = [google_project_service.services]
}

resource "google_sql_database" "database" {
  name     = "rag_memory"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "users" {
  name     = "rag_admin"
  instance = google_sql_database_instance.postgres.name
  password = var.db_password
}
