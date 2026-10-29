# --- AUTO-INGESTION (EVENTARC TRIGGER) ---

# 1. The Ingestion Service (Receives the trigger)
resource "google_cloud_run_v2_service" "ingestion" {
  name     = "${var.app_name}-ingestion"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY" # Only Eventarc can talk to this

  template {
    service_account = google_service_account.ingestion_sa.email

    scaling {
      max_instance_count = 3
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.app_name}-repo/ingestion:latest"

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        startup_cpu_boost = true  # extra CPU while heavy libraries (Vertex AI) load
        cpu_idle          = false # keep CPU after responding: Eventarc requests return 'accepted' and parsing continues in a background task
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
        name  = "QDRANT_CLUSTER_ENDPOINT"
        value = var.qdrant_url
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
        name  = "PDF_PARSER"
        value = var.pdf_parser
      }
      env {
        name  = "GCP_DOC_AI_PROCESSOR_ID"
        value = reverse(split("/", google_document_ai_processor.ocr.name))[0]
      }
      env {
        name  = "GCP_DOC_AI_LOCATION"
        value = google_document_ai_processor.ocr.location
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
        for_each = local.ingestion_secret_env
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
    google_project_iam_member.ingestion_roles
  ]
}



# 2. Eventarc Trigger (Watches GCS)
resource "google_eventarc_trigger" "gcs_trigger" {
  name     = "rag-gcs-trigger"
  location = var.region

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.raw_data.name
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.ingestion.name
      region  = var.region
    }
  }

  service_account = google_service_account.ingestion_sa.email

  depends_on = [
    google_project_iam_member.ingestion_roles,
    google_project_iam_member.gcs_pubsub_publishing,
    google_project_iam_member.eventarc_service_agent
  ]
}
