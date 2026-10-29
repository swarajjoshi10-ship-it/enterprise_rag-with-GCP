# --- GCP PROVIDER ---
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Only used for google_project_service_identity (beta-only in provider 5.x)
provider "google-beta" {
  project = var.project_id
  region  = var.region
}
