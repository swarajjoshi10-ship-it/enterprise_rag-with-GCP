# 21. Deployment Runbook (Terraform, Option B)

Step-by-step deployment to GCP project `enterprise-rag-499816`, with cost controls.
Run every command from the repository root unless noted.

## Cost profile

| Resource | Default | ~$/month |
|---|---|---|
| Cloud SQL Postgres (db-f1-micro + 10 GB) | on | ~$10 |
| Cloud Run x3 (scale to zero, max 2 instances) | on | ~$0 at demo traffic |
| Artifact Registry, GCS, Eventarc, Secret Manager, Cloud Build | on | < $2 |
| Memorystore Redis 7.2 (semantic cache) | **off** (`enable_redis = false`) | ~$36 when on |
| Document AI OCR | **off** (`pdf_parser = "pypdf"`) | $1.50 per 1,000 pages when on |

Set a budget alert first: Console → Billing → Budgets & alerts → $25, alerts at 50/90/100%.

## 0. One-time setup

```bash
winget install Hashicorp.Terraform
gcloud auth login
gcloud auth application-default login
gcloud config set project enterprise-rag-499816
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # then fill in your keys
```

`terraform.tfvars` and `terraform.tfstate` are gitignored and contain your keys: never commit them.

## 1. Stage 1: APIs, registry, build permissions

Cloud Run needs container images to exist, so create the registry first.
The quotes around each `-target` are required in Windows PowerShell, which otherwise splits the argument at the dot.

```bash
cd terraform
terraform init
terraform plan "-target=google_project_service.services" "-target=google_artifact_registry_repository.repo" "-target=google_project_iam_member.cloudbuild_builder"
terraform apply "-target=google_project_service.services" "-target=google_artifact_registry_repository.repo" "-target=google_project_iam_member.cloudbuild_builder"
cd ..
```

## 2. Build the three images with Cloud Build

No local Docker needed. `.gcloudignore`/`.dockerignore` keep `.env`, `.venv` and `DATA/` out of the upload.

```bash
gcloud builds submit --config cloudbuild.yaml .
```

## 3. Stage 2: everything else

```bash
cd terraform
terraform plan
terraform apply
terraform output
```

Cloud SQL creation takes 5-10 minutes.

## 4. Smoke test

```bash
gcloud storage cp DATA/true_data/cronjobs.docx gs://enterprise-rag-499816-rag-raw/true/cronjobs.docx
gcloud run services logs read enterprise-rag-ingestion --region us-central1 --limit 30
```

Then open the `ui_url` output and ask: "How do I create a CronJob in Kubernetes?"

## 5. Pause and resume (save money between sessions)

```bash
# Stop Cloud SQL compute (storage still billed, ~$2/month)
gcloud sql instances patch enterprise-rag-db --activation-policy=NEVER
# Resume
gcloud sql instances patch enterprise-rag-db --activation-policy=ALWAYS
```

Semantic-cache experiment week: set `enable_redis = true` in `terraform.tfvars`, `terraform apply`; set it back to `false` and apply when done.

## 6. Tear down everything

```bash
cd terraform
terraform destroy
```

Buckets use `force_destroy = true`, so their contents are deleted too.
