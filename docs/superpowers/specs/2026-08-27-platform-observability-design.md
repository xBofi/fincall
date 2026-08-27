# Platform Observability Design

## Goal

Create a repository-agnostic observability and DevSecOps platform that can be consumed by Building Call Assistant, Obsidian-RAG, and future applications. The platform is hosted in GitHub and exposes safe local lifecycle commands for development plus repeatable OpenTofu infrastructure for preproduction and production.

## Repository and Promotion Model

The repository is named `platform-observability` and is created under the `xBofi` GitHub account. It uses three long-lived branches:

```text
develop -> preproduction -> production
```

All commits are made to `develop`; merges promote to `preproduction` and then `production`. `production` is the default branch and represents deployable state. GitHub Actions validates changes and publishes images only after the relevant quality and security gates pass.

## Service Boundaries

The platform has independent deployable components:

- **Langfuse:** LLM traces, prompt management, prompt versions, datasets and evaluations.
- **SonarQube Community Build:** persistent code-quality and static-analysis server. Application CI jobs run scanners against it.
- **GlitchTip:** persistent error-monitoring service compatible with Sentry SDKs.
- **Security tools:** ephemeral CI jobs, not platform services. OWASP dep-scan scans dependency manifests and images; OWASP ZAP baseline/API scans target applications only in preproduction.

No application business request may fail merely because Langfuse or GlitchTip is unavailable. SDK integrations must be asynchronous/best-effort, have bounded timeouts, and be disableable through environment configuration. Prompt retrieval must use a versioned, cached client with a safe fallback policy specified by each application.

## Development Lifecycle

Langfuse is the only service designed for explicit per-session local operation. Shared PowerShell commands provide `up`, `down`, `status`, and `logs` operations. `down` stops containers but never uses volume deletion; local data survives between sessions. Startup waits for health checks and reports the endpoint, while shutdown is idempotent.

Codex and Claude do not implement their own lifecycle logic. They are given client-specific instructions that invoke the same repository scripts. When Langfuse is required for prompt work, agents must start it first; when it is not required, it remains stopped. Langfuse's Agent Skill or MCP integration is connected only after a running instance and credentials are available.

SonarQube and GlitchTip can be started locally through Compose for integration checks, but their normal role is persistent service operation in preproduction/production. They are never required for a developer to run unit tests.

## Prompt Versioning

Langfuse is the system of record for runtime prompts. Prompt names are stable; every change creates a version. Labels map prompts to environments: `develop`, `preproduction`, and `production`. A prompt change is tested against a versioned dataset before its label is promoted. Application source stores prompt identifiers and fallback behavior, but not mutable prompt text once the prompt has been migrated.

## Deployment Architecture

Docker Compose runs local development profiles. Docker containers remain the application packaging format on Ubuntu.

OpenTofu manages preproduction and production infrastructure: compute, network exposure, firewall/security groups, persistent volumes or managed databases, DNS/TLS integration variables, and encrypted secret references. OpenTofu does not embed secrets in state or source. Server configuration and container deployment are performed through a separate, idempotent deployment layer invoked after OpenTofu provisioning.

The initial production target is a single Ubuntu server with isolated persistent volumes and reverse-proxy/TLS support. The design remains compatible with a later Kubernetes migration without requiring applications to change their telemetry endpoints.

## CI/CD and Security Gates

GitHub Actions runs lint, configuration validation, Compose validation, OpenTofu formatting/validation/plan, secret scanning, dependency and image scans, and Docker image builds. OpenTofu apply and any deployment require the matching protected environment and occur only after a merge to `preproduction` or `production`.

OWASP dep-scan runs on every relevant change. OWASP ZAP uses an explicit target URL and runs only against an isolated preproduction deployment; active scans never target production. SonarQube scanners run from each consumer repository's CI and use a token stored in GitHub environment secrets. Findings use severity thresholds and a documented suppression process; failures are never silently ignored.

## Repository Layout

```text
docs/
  architecture/
  operations/
  security/
compose/
  langfuse/
  local/
infra/
  opentofu/
    modules/
    environments/develop/
    environments/preproduction/
    environments/production/
scripts/
  langfuse-up.ps1
  langfuse-down.ps1
  langfuse-status.ps1
  langfuse-logs.ps1
  validate-platform.ps1
.github/workflows/
  validate.yml
  security.yml
  deploy-preproduction.yml
  deploy-production.yml
agent-instructions/
  CODEX.md
  CLAUDE.md
```

The repository never stores live `.env` files, token values, private keys, OpenTofu state, scans containing sensitive application payloads, or generated service volumes.

## Success Criteria

- A developer can start, inspect, and stop local Langfuse without deleting data.
- Codex and Claude follow the same shared lifecycle scripts and prompt-management contract.
- SonarQube and GlitchTip are deployable as persistent services, but applications degrade safely if telemetry is unavailable.
- GitHub Actions validates the platform and executes applicable security checks.
- Preproduction and production can be provisioned repeatably with OpenTofu, without committing secrets.
- No scan targets production unless it is explicitly a passive, approved monitoring integration.
