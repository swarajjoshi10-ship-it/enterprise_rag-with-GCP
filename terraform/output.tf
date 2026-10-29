output "backend_url" {
  value = google_cloud_run_v2_service.backend.uri
}

output "ui_url" {
  value = google_cloud_run_v2_service.ui.uri
}

output "raw_bucket" {
  value = google_storage_bucket.raw_data.name
}

output "db_connection_name" {
  value = google_sql_database_instance.postgres.connection_name
}

output "doc_ai_processor_id" {
  value = reverse(split("/", google_document_ai_processor.ocr.name))[0]
}

output "redis_host" {
  value = var.enable_redis ? google_redis_instance.cache[0].host : "disabled (enable_redis = false)"
}

output "artifact_repo" {
  value = google_artifact_registry_repository.repo.name
}
