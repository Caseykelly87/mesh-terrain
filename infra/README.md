# infra

Terraform for the cloud phase: S3, RDS Postgres with PostGIS and h3-pg, one EC2 host running Compose, IAM with OIDC for GitHub Actions, and SSM for secrets. Nothing here yet; created in Phase 6 by platform-eng.

Run: `terraform plan` on pull requests through CI; `apply` only after Casey approves the monthly cost stated in the PR.

Deliberately not here: anything that runs locally. The Compose stack in the repository root is the local environment.
