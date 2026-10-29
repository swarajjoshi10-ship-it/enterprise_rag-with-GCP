# --- CORE INFRASTRUCTURE ---

# 1. Enable Required APIs
resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "aiplatform.googleapis.com",
    "pubsub.googleapis.com",
    "eventarc.googleapis.com",
    "redis.googleapis.com",
    "compute.googleapis.com",
    "storage.googleapis.com",
    "documentai.googleapis.com"
  ])
  service            = each.key
  disable_on_destroy = false
}

# 2. Get Project Data
data "google_project" "project" {
  project_id = var.project_id
}

# 3. Networking (only needed to reach Redis' private IP)
# Named after app_name so it never collides with a manually created "rag-vpc"
resource "google_compute_network" "rag_vpc" {
  count                   = var.enable_redis ? 1 : 0
  name                    = "${var.app_name}-vpc"
  auto_create_subnetworks = true
  depends_on              = [google_project_service.services]
}

# 4. Redis Instance (Memorystore) - optional, see var.enable_redis
# Vector search (needed by the semantic cache) requires Redis 7.2+
resource "google_redis_instance" "cache" {
  count              = var.enable_redis ? 1 : 0
  name               = "rag-cache"
  tier               = "BASIC"
  memory_size_gb     = 1
  location_id        = "${var.region}-a"
  authorized_network = google_compute_network.rag_vpc[0].id

  redis_version = "REDIS_7_2"
  display_name  = "RAG Semantic Cache"

  depends_on = [google_project_service.services]
}

# 5. Service Accounts (one per microservice, least privilege)
resource "google_service_account" "ingestion_sa" {
  account_id   = "rag-ingestion-sa"
  display_name = "RAG Ingestion Service Account"
}

resource "google_service_account" "backend_sa" {
  account_id   = "rag-backend-sa"
  display_name = "RAG Backend Service Account"
}

resource "google_service_account" "ui_sa" {
  account_id   = "rag-ui-sa"
  display_name = "RAG UI Service Account"
}

# Grant SA permission to read from bucket and write to logs
resource "google_project_iam_member" "ingestion_roles" {
  for_each = toset([
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
    "roles/aiplatform.user",
    "roles/eventarc.eventReceiver",
    "roles/run.invoker",
    "roles/documentai.apiUser"
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.ingestion_sa.email}"
}

# Backend: Vertex embeddings + Cloud SQL connection
resource "google_project_iam_member" "backend_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/cloudsql.client"
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.backend_sa.email}"
}

# Cloud Build runs as the Compute Engine default SA in new projects,
# which no longer gets Editor automatically: let it build and push images
resource "google_project_iam_member" "cloudbuild_builder" {
  project    = var.project_id
  role       = "roles/cloudbuild.builds.builder"
  member     = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  depends_on = [google_project_service.services]
}

# 6. GCS Permissions for Eventarc
# GCS needs to be able to publish to PubSub to trigger Eventarc
data "google_storage_project_service_account" "gcs_account" {
  project = var.project_id
}

resource "google_project_iam_member" "gcs_pubsub_publishing" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs_account.email_address}"
}

# 7. Eventarc Service Agent Permissions
# Google creates this agent lazily, so force it to exist before granting it a role
resource "google_project_service_identity" "eventarc" {
  provider   = google-beta
  project    = var.project_id
  service    = "eventarc.googleapis.com"
  depends_on = [google_project_service.services]
}

# The internal Eventarc account needs to be able to "Get" buckets to validate them
resource "google_project_iam_member" "eventarc_service_agent" {
  project = var.project_id
  role    = "roles/eventarc.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.eventarc.email}"
}

# 8. Storage Buckets
resource "google_storage_bucket" "raw_data" {
  name                        = "${var.project_id}-rag-raw"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

resource "google_storage_bucket" "processed_data" {
  name                        = "${var.project_id}-rag-processed"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

# 9. Artifact Registry for Docker Images
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "${var.app_name}-repo"
  description   = "Docker repository for RAG Microservices"
  format        = "DOCKER"
  depends_on    = [google_project_service.services]
}

# 10. Document AI OCR processor (only billed when PDF_PARSER=docai processes pages)
resource "google_document_ai_processor" "ocr" {
  location     = "us"
  type         = "OCR_PROCESSOR"
  display_name = "rag-ocr"
  depends_on   = [google_project_service.services]
}
