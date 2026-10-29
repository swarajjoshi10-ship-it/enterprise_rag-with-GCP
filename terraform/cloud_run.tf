
# --- CLOUD RUN SERVICES ---

# 1. Backend API Service
resource "google_cloud_run_v2_service" "backend" {
  name     = "${var.app_name}-backend"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.backend_sa.email

    scaling {
      max_instance_count = var.max_instances
    }

    # Direct VPC egress is only needed to reach Redis' private IP
    dynamic "vpc_access" {
      for_each = var.enable_redis ? [1] : []
      content {
        network_interfaces {
          network = google_compute_network.rag_vpc[0].name
        }
        egress = "PRIVATE_RANGES_ONLY"
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.postgres.connection_name]
      }
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.app_name}-repo/backend:latest"

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        startup_cpu_boost = true # extra CPU while heavy libraries (Vertex AI) load
        cpu_idle          = true # request-based billing: CPU only while serving requests
      }

      # Allow up to 5 min to start: the first start of a new image is slow
      startup_probe {
        period_seconds    = 10
        timeout_seconds   = 5
        failure_threshold = 30
        tcp_socket {
          port = 8080
        }
      }

      env {
        name  = "LOCAL_MODE"
        value = "false"
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "LOCATION"
        value = var.region
      }
      env {
        name  = "GCP_RAW_BUCKET"
        value = google_storage_bucket.raw_data.name
      }
      env {
        name  = "GCP_PROCESSED_BUCKET"
        value = google_storage_bucket.processed_data.name
      }
      env {
        name  = "GROQ_MODEL"
        value = var.groq_model
      }
      env {
        name  = "QDRANT_CLUSTER_ENDPOINT"
        value = var.qdrant_url
      }
      env {
        name  = "DB_CONNECTION_NAME"
        value = google_sql_database_instance.postgres.connection_name
      }
      env {
        name  = "DB_USER"
        value = google_sql_user.users.name
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.database.name
      }
      dynamic "env" {
        for_each = var.enable_redis ? { REDIS_HOST = google_redis_instance.cache[0].host, REDIS_PORT = "6379" } : {}
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "LANGSMITH_TRACING"
        value = local.langsmith_tracing
      }
      env {
        name  = "LANGSMITH_PROJECT"
        value = var.langsmith_project
      }
      env {
        name  = "LANGSMITH_ENDPOINT"
        value = "https://api.smith.langchain.com"
      }
      dynamic "env" {
        for_each = local.backend_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_version.app,
    google_secret_manager_secret_iam_member.access,
    google_project_iam_member.backend_roles
  ]
}


# 2. UI Service
resource "google_cloud_run_v2_service" "ui" {
  name     = "${var.app_name}-ui"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.ui_sa.email

    scaling {
      max_instance_count = var.max_instances
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.app_name}-repo/ui:latest"

      ports {
        container_port = 8501
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        startup_cpu_boost = true # extra CPU while heavy libraries (Vertex AI) load
        cpu_idle          = true # request-based billing: CPU only while serving requests
      }

      # Allow up to 5 min to start: the first start of a new image is slow
      startup_probe {
        period_seconds    = 10
        timeout_seconds   = 5
        failure_threshold = 30
        tcp_socket {
          port = 8501
        }
      }

      env {
        name  = "BACKEND_URL"
        value = google_cloud_run_v2_service.backend.uri
      }
      env {
        name  = "LANGSMITH_TRACING"
        value = local.langsmith_tracing
      }
      env {
        name  = "LANGSMITH_PROJECT"
        value = var.langsmith_project
      }
      env {
        name  = "LANGSMITH_ENDPOINT"
        value = "https://api.smith.langchain.com"
      }
      dynamic "env" {
        for_each = local.ui_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_version.app,
    google_secret_manager_secret_iam_member.access
  ]
}



# --- PUBLIC ACCESS (Unauthenticated for Demo; capped by var.max_instances) ---
resource "google_cloud_run_v2_service_iam_member" "backend_public" {
  name     = google_cloud_run_v2_service.backend.name
  location = google_cloud_run_v2_service.backend.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "ui_public" {
  name     = google_cloud_run_v2_service.ui.name
  location = google_cloud_run_v2_service.ui.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
