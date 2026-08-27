# Application Governance and Knowledge Access Design

## Goal

Prepare the future Building Call Assistant application repository with focused, durable instructions for human and AI contributors. The application consumes Obsidian-RAG as its single knowledge infrastructure; it does not embed or duplicate RAG logic.

## Repository Boundaries

`Building-Call-Assistant` contains product code, product documentation, and client-side instructions for accessing the knowledge base. `Obsidian-RAG` owns the MCP server, the local index, GraphRAG evaluation, and vault access. The Obsidian vault remains the Markdown source of truth.

Contributors query Obsidian-RAG for broad or cross-document context. For a question tied to a named note, they read that Markdown note through the MCP. Any vault write is performed through the MCP tools, never by treating derived RAG data as a source of truth.

## Contributor Contract

`AGENTS.md` is a concise routing document. It requires Superpowers for architecture, planning, test-first implementation, debugging, and verification. It points contributors to topical documents rather than restating policies.

The documentation hierarchy is:

```text
docs/
  architecture/     system design and ADRs
  development/      stack, coding rules, tests and quality gates
  knowledge/        Obsidian-RAG usage and vault-write rules
  operations/       containers, CI/CD, deployment and observability
  product/          business rules and acceptance criteria
  security/         JWT, secret handling and security review rules
  integrations/     voice agents, LiteLLM and external service contracts
```

Each document has a focused responsibility and links to related material. Business rules are maintained in `docs/product/` and must drive the tests; tests must not redefine a rule to accommodate an implementation.

## Required Technology Baseline

- Frontend: React, TypeScript, Vite, Ant Design.
- Backend: Python, FastAPI, JWT, SQLAlchemy, Alembic, PostgreSQL, `uv`.
- Runtime: Docker containers on Ubuntu.
- CI/CD target: GitLab CI/CD and GitLab container registry; GitHub Actions may be used only for supplementary public/open-source automation unless the platform decision changes.
- Quality and tests: pytest, Ruff, mypy, Bandit, unit, integration, business-rule and controlled E2E tests.
- Observability: structured logs to Seq, SonarQube quality analysis, Langfuse for LLM traces, and GlitchTip error reporting.
- AI: LiteLLM is the preferred model proxy where latency measurements show it is acceptable for the use case.
- Infrastructure: evaluate OpenTofu before provisioning shared infrastructure.

## Git and Environment Promotion

The application repository uses exactly three long-lived branches:

```text
develop -> preproduction -> production
```

Work commits are created on `develop`. Promotion occurs only by merge from `develop` to `preproduction`, then from `preproduction` to `production`. `production` is the default branch and represents deployable production state. No direct commits are allowed on `preproduction` or `production`.

Each branch maps to the matching deployment environment when infrastructure is available. Before infrastructure exists, the same branch policy applies but deployment jobs are disabled or validate configuration only.

## CI/CD Shape

Pipelines run tests, quality/security checks, static analysis, and Docker image builds. They use `develop` for development checks, `preproduction` for release-candidate validation, and `production` for controlled release deployment. Cost-bearing E2E flows involving paid APIs or LLMs run only in the controlled preproduction gate before production.

## Security and Configuration

Secrets never enter Git, RAG indexes, Markdown source notes, logs, or agent responses. JWT configuration, database credentials, provider keys, Seq/Langfuse/GlitchTip endpoints, and deployment secrets are injected through the deployment environment. Security reviews use the available Codex security capability before production promotion.

## Success Criteria

- A contributor can identify the right documentation file and development process from `AGENTS.md`.
- A contributor knows when to query RAG, when to read a specific Markdown note, and how to safely edit a note.
- All required technical decisions have an authoritative, focused document.
- Branches and documentation consistently use `develop`, `preproduction`, and `production`.
- The application contains no copy of Obsidian-RAG indexing or vault synchronization code.
