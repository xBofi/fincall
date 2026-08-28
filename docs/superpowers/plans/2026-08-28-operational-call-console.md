# Operational Call Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build the production-grade operational call console: Google authentication, RBAC, durable calls and audio, 14-day retention, and a Hallmark-designed workbench.

**Architecture:** FastAPI owns Google OIDC, revocable secure sessions, PostgreSQL operational data, and role checks. Audio is held in private S3-compatible object storage through a port; server-side Piper TTS produces an archiveable assistant track. React consumes authenticated, paginated operational APIs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, PostgreSQL, Authlib, S3-compatible storage, Piper TTS, React 19, TypeScript, Vite, Ant Design, Playwright.

**Spec:** docs/superpowers/specs/2026-08-28-operational-call-console-design.md

## Global Constraints

- Google OAuth uses Authorization Code + PKCE; browser storage never holds a Google token.
- Sessions are HttpOnly, Secure, SameSite=Lax, revocable and verified per request.
- Only roles owner, administrador, calidad exist; identities are denied until authorized.
- PostgreSQL is source of truth for calls, turns, decisions, users, sessions and audit entries.
- Audio is private, authorized server-side and deleted at 14 days.
- UI uses named OKLCH tokens, does not invent metrics, honors reduced motion and has no horizontal scroll at 320/375/414/768 px.

---

## File map

| Path | Purpose |
| --- | --- |
| backend/src/building_call/domain/operaciones.py | Roles and durable operational Pydantic models. |
| backend/src/building_call/auth.py | Google OIDC, sessions and FastAPI RBAC dependencies. |
| backend/src/building_call/repos/operaciones.py | PostgreSQL ledger, query and audit repository. |
| backend/src/building_call/almacen_audio.py | Storage port with S3 and development-local adapters. |
| backend/src/building_call/tts.py | Piper TTS adapter and archiveable assistant audio. |
| backend/src/building_call/api/operaciones.py | Protected operational and user-management routes. |
| backend/alembic/versions/0002_operaciones_y_acceso.py | Additive security and operational schema. |
| frontend/src/auth.tsx | Session provider and Google sign-in gate. |
| frontend/src/components/Operaciones/ | Metrics, queue, dossier, decision flow and player. |
| frontend/src/tokens.css | Hallmark visual tokens. |

### Task 1: Model and migrate durable access and call operations

**Files:**
- Create: backend/src/building_call/domain/operaciones.py
- Modify: backend/src/building_call/repos/modelos_sql.py
- Create: backend/alembic/versions/0002_operaciones_y_acceso.py
- Test: backend/tests/test_operaciones_dominio.py

**Interfaces:** Produces Rol, Usuario, Sesion, TurnoOperacion, DecisionOperacion, PistaAudio and EventoAuditoria.

- [ ] **Step 1: Write the failing contract tests**

    def test_roles_are_closed() -> None:
        assert Rol("calidad") is Rol.calidad
        with pytest.raises(ValueError): Rol("operador")

    def test_audio_expiry_must_follow_creation() -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            PistaAudio(llamada_id="llam-1", canal="cliente", creada_en=now, expira_en=now)

- [ ] **Step 2: Run the test to verify it fails**

Run: cd backend; uv run pytest tests/test_operaciones_dominio.py -v
Expected: FAIL because domain.operaciones does not exist.

- [ ] **Step 3: Add domain models and the additive migration**

    class Rol(str, Enum):
        owner = "owner"
        administrador = "administrador"
        calidad = "calidad"

Create users, sessions, call turns, decisions, audio tracks and audit events. Add state, completed_at and requires_review to calls; index state/start time, call/turn and audio expiry.

- [ ] **Step 4: Verify and commit**

Run: cd backend; uv run pytest tests/test_operaciones_dominio.py -v; uv run alembic upgrade head
Expected: PASS and revision 0002.

Run: git add backend/src/building_call/domain/operaciones.py backend/src/building_call/repos/modelos_sql.py backend/alembic/versions/0002_operaciones_y_acceso.py backend/tests/test_operaciones_dominio.py; git commit -m "feat: add operational access schema"

### Task 2: Implement PostgreSQL operations repository and durable call manager

**Files:**
- Create: backend/src/building_call/repos/operaciones.py
- Modify: backend/src/building_call/servicios.py
- Modify: backend/src/building_call/metricas.py
- Modify: backend/src/building_call/api/main.py
- Test: backend/tests/test_repositorio_operaciones.py
- Test: backend/tests/test_persistencia_llamada.py

**Interfaces:** Produces RepositorioOperaciones.listar_llamadas, expediente, guardar_turno, guardar_decision, registrar_auditoria, GestorLlamadas.obtener_durable and finalizar.

- [ ] **Step 1: Write failing persistence tests**

    async def test_unresolved_filter_uses_persisted_calls(repo):
        await repo.guardar_llamada(llamada("ok", resultado="incidencia_creada"))
        await repo.guardar_llamada(llamada("review", resultado="no_clasificable"))
        page = await repo.listar_llamadas(resultado="no_clasificable", limite=25, cursor=None)
        assert [item.id for item in page.items] == ["review"]

    async def test_manager_reads_call_after_restart(repo):
        first = GestorLlamadas(deps, operaciones=repo)
        view = await first.iniciar()
        await first.mensaje(view["llamada_id"], "Hay una fuga")
        assert (await GestorLlamadas(deps, operaciones=repo).obtener_durable(view["llamada_id"])).turnos

- [ ] **Step 2: Run tests to verify they fail**

Run: cd backend; uv run pytest tests/test_repositorio_operaciones.py tests/test_persistencia_llamada.py -v
Expected: FAIL because state and metrics are in memory only.

- [ ] **Step 3: Implement transactional persistence**

After every graph execution persist history, trace, interpretation, validations and timing in one transaction. Write immutable decision rows; close call on graph finish or hang-up; hydrate memory from durable state on a miss. Aggregate p50/p95 and resolution counts from stored rows, not RegistroMetricas memory.

- [ ] **Step 4: Verify and commit**

Run: cd backend; uv run pytest tests/test_repositorio_operaciones.py tests/test_persistencia_llamada.py tests/test_api.py -v
Expected: PASS including manager restart.

Run: git add backend/src/building_call/repos/operaciones.py backend/src/building_call/servicios.py backend/src/building_call/metricas.py backend/src/building_call/api/main.py backend/tests/test_repositorio_operaciones.py backend/tests/test_persistencia_llamada.py; git commit -m "feat: persist call decisions and metrics"

### Task 3: Add Google sign-in, sessions and RBAC

**Files:**
- Modify: backend/pyproject.toml
- Modify: backend/src/building_call/settings.py
- Create: backend/src/building_call/auth.py
- Modify: backend/src/building_call/api/main.py
- Test: backend/tests/test_auth.py

**Interfaces:** Produces usuario_actual, exigir_roles(*roles), and routes /api/sesion, /api/auth/google/iniciar, /api/auth/google/callback, /api/auth/salir.

- [ ] **Step 1: Write failing authorization tests**

    async def test_quality_cannot_change_a_role(client_calidad):
        response = await client_calidad.patch("/api/usuarios/user-2", json={"rol": "administrador"})
        assert response.status_code == 403

    async def test_unknown_google_identity_is_denied(client, fake_google):
        fake_google.identity(email="unknown@example.com", sub="unknown")
        assert (await client.get("/api/auth/google/callback?code=x&state=ok")).status_code == 403

- [ ] **Step 2: Run tests to verify they fail**

Run: cd backend; uv run pytest tests/test_auth.py -v
Expected: FAIL because auth routes do not exist.

- [ ] **Step 3: Implement secure auth**

Add authlib>=1.4 and settings BC_GOOGLE_CLIENT_ID, BC_GOOGLE_CLIENT_SECRET, BC_GOOGLE_REDIRECT_URI, BC_SESSION_SECRET, BC_SESSION_TTL_MINUTES=480, BC_BOOTSTRAP_OWNER_EMAIL. Exchange OAuth code with PKCE; validate issuer, audience and nonce; hash opaque session IDs before PostgreSQL storage; set raw ID only in an HttpOnly, Secure, SameSite=Lax cookie. Bootstrap only configured owner when no owner exists.

- [ ] **Step 4: Enforce and audit**

Quality is read-only; administrator manages non-owner users; only owner assigns owner. Audit login, denial, logout, role change and session revocation.

- [ ] **Step 5: Verify and commit**

Run: cd backend; uv run pytest tests/test_auth.py -v; uv run ruff check src tests; uv run mypy src
Expected: PASS.

Run: git add backend/pyproject.toml backend/src/building_call/settings.py backend/src/building_call/auth.py backend/src/building_call/api/main.py backend/tests/test_auth.py; git commit -m "feat: add Google authentication and RBAC"

### Task 4: Archive complete call audio and purge it after 14 days

**Files:**
- Modify: backend/pyproject.toml
- Modify: backend/src/building_call/settings.py
- Create: backend/src/building_call/almacen_audio.py
- Create: backend/src/building_call/retencion.py
- Create: backend/src/building_call/tts.py
- Modify: backend/src/building_call/api/main.py
- Modify: frontend/src/voz.ts
- Modify: frontend/src/App.tsx
- Test: backend/tests/test_audio_y_retencion.py

**Interfaces:** Produces AlmacenAudio.guardar, url_reproduccion, eliminar, PurgadorRetencion.ejecutar and SintetizadorVoz.sintetizar.

- [ ] **Step 1: Write failing audio tests**

    async def test_quality_gets_short_lived_url(client_calidad):
        body = (await client_calidad.get("/api/llamadas/llam-1/audio?canal=cliente")).json()
        assert body["url"]
        assert body["expira_en"] > body["emitida_en"]

    async def test_retention_deletes_object_and_metadata(store, repo):
        assert await PurgadorRetencion(repo, store).ejecutar(datetime.now(UTC)) == 1
        assert store.deleted_keys == ["llam-1/cliente.webm"]

- [ ] **Step 2: Run tests to verify they fail**

Run: cd backend; uv run pytest tests/test_audio_y_retencion.py -v
Expected: FAIL because storage and retention ports do not exist.

- [ ] **Step 3: Implement tracks and server TTS**

Add S3 configuration and a development-local adapter. Reject MIME outside audio/webm, audio/ogg, audio/wav, audio/mpeg; enforce BC_AUDIO_MAX_BYTES; checksum before save; issue 300-second URLs only after RBAC and audit. Add BC_TTS=piper and BC_PIPER_MODEL_PATH; synthesize and save assistant audio before return. Upload each client MediaRecorder turn; use HTMLAudioElement for recorded calls and pause it on barge-in.

- [ ] **Step 4: Purge, verify and commit**

Delete expired object then metadata idempotently and write audio.eliminado_retencion. Expose a scheduler-only purge route.

Run: cd backend; uv run pytest tests/test_audio_y_retencion.py -v; uv run bandit -r src; cd ../frontend; npm run build
Expected: PASS.

Run: git add backend frontend; git commit -m "feat: archive synchronized call audio"

### Task 5: Publish protected operational APIs

**Files:**
- Create: backend/src/building_call/api/operaciones.py
- Modify: backend/src/building_call/api/main.py
- Test: backend/tests/test_api_operaciones.py

**Interfaces:** Produces GET /api/operaciones/resumen, GET /api/operaciones/llamadas, GET /api/operaciones/llamadas/{id}, and GET/PATCH /api/usuarios.

- [ ] **Step 1: Write failing API tests**

    async def test_quality_reads_dossier_but_cannot_start_call(client_calidad):
        assert (await client_calidad.get("/api/operaciones/llamadas/llam-1")).status_code == 200
        assert (await client_calidad.post("/api/llamadas", json={})).status_code == 403

    async def test_summary_uses_null_for_missing_latency(client_admin):
        assert (await client_admin.get("/api/operaciones/resumen")).json()["respuesta_ms"]["p50"] is None

- [ ] **Step 2: Run tests to verify they fail**

Run: cd backend; uv run pytest tests/test_api_operaciones.py -v
Expected: FAIL because the operational router does not exist.

- [ ] **Step 3: Implement filters and read models**

Accept desde, hasta, estado, resultado, requiere_revision, latencia_min_ms, limite 1..100 and opaque keyset cursor. Return ISO timestamps and next_cursor; reject invalid ranges with 422. Dossier reads and audio URL issuance must create audit events.

- [ ] **Step 4: Verify and commit**

Run: cd backend; uv run pytest tests/test_api_operaciones.py tests/test_auth.py -v
Expected: PASS.

Run: git add backend/src/building_call/api/operaciones.py backend/src/building_call/api/main.py backend/tests/test_api_operaciones.py; git commit -m "feat: expose operational call APIs"

### Task 6: Create the Hallmark-authenticated operations workbench

**Files:**
- Create: frontend/src/tokens.css
- Modify: frontend/src/index.css
- Create: frontend/src/auth.tsx
- Create: frontend/src/components/LoginGoogle.tsx
- Create: frontend/src/components/Operaciones/MetricasOperacion.tsx
- Create: frontend/src/components/Operaciones/ColaLlamadas.tsx
- Create: frontend/src/components/Operaciones/ExpedienteLlamada.tsx
- Create: frontend/src/components/Operaciones/ReproductorLlamada.tsx
- Create: frontend/src/components/Operaciones/FlujoDecision.tsx
- Modify: frontend/src/App.tsx
- Modify: frontend/src/api.ts
- Test: frontend/src/components/Operaciones/operaciones.test.tsx

**Interfaces:** Consumes Task 5 APIs; produces selected-call workbench while retaining live calling in the right pane.

- [ ] **Step 1: Write failing UI tests**

    it("selects an unresolved call and opens its dossier", async () => {
      render(<Operaciones />)
      await userEvent.click(await screen.findByText("llam-review"))
      expect(await screen.findByRole("heading", { name: /detalle de llamada/i })).toBeVisible()
    })

    it("does not prefetch an audio URL before play", () => {
      render(<ReproductorLlamada pistas={[pista]} />)
      expect(fetch).not.toHaveBeenCalledWith(expect.stringContaining("/audio"))
    })

- [ ] **Step 2: Run tests to verify they fail**

Run: cd frontend; npm run test -- operaciones.test.tsx
Expected: FAIL because components do not exist.

- [ ] **Step 3: Implement chosen three-pane workbench**

Create anonymous Google sign-in gate and role-aware shell. First line of tokens.css is a Hallmark Workbench stamp; define all colors, fonts, spacing and motion as named tokens. Use queue left, dossier center, live call right. Show API values only and em dash for missing data. Link player position to speaker transcript, latency turns and decision flow. Disable quality actions client-side as defense in depth.

- [ ] **Step 4: Implement responsive and accessible motion**

At widths below 768px, render queue, dossier and live panel in one ordered column with no scrolling tab strip. Animate only opacity/transform for selection, data arrival and playback; reduced motion means opacity no longer than 150ms. Label controls and show focus ring.

- [ ] **Step 5: Verify and commit**

Run: cd frontend; npm run test -- operaciones.test.tsx; npm run lint; npm run build
Expected: PASS.

Run: git add frontend/src; git commit -m "feat: build operational call workbench"

### Task 7: End-to-end verification, Hallmark audit and deployment instructions

**Files:**
- Create: frontend/e2e/operaciones.spec.ts
- Modify: frontend/package.json
- Modify: frontend/README.md
- Modify: backend/README.md
- Create: .hallmark/log.json

**Interfaces:** Proves roles, retention, recording and responsive requirements.

- [ ] **Step 1: Write failing browser tests**

    test("quality can inspect but cannot initiate calls", async ({ page }) => {
      await loginAs(page, "calidad@example.com")
      await page.getByText("llam-review").click()
      await expect(page.getByRole("button", { name: /nueva llamada/i })).toBeDisabled()
    })

    for (const width of [320, 375, 414, 768]) test("no x-scroll " + width, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      expect(await page.locator("html").evaluate(el => el.scrollWidth <= el.clientWidth)).toBeTruthy()
    })

- [ ] **Step 2: Run tests to verify they fail**

Run: cd frontend; npx playwright test e2e/operaciones.spec.ts
Expected: FAIL until authenticated workbench is complete.

- [ ] **Step 3: Add scripts and operational documentation**

Add test:e2e. Document Google redirect configuration, required variables, migration, S3 bucket policy, Piper model provisioning, daily retention scheduler and owner bootstrap. Do not document secret values.

- [ ] **Step 4: Audit and verify the complete product**

Run: hallmark audit frontend/src; cd backend; uv run pytest; uv run ruff check src tests; uv run mypy src; cd ../frontend; npm run lint; npm run build; npm run test; npm run test:e2e
Expected: all PASS. Add .hallmark/log.json after the audit passes: macrostructure Workbench, theme Cobalt, enrichment none, brief Operational call console.

- [ ] **Step 5: Commit**

Run: git add frontend backend .hallmark/log.json; git commit -m "test: verify operational call console"

## Plan self-review

- Spec coverage: Tasks 1–3 implement roles, PostgreSQL and authentication; Tasks 4–5 implement private audio, retention and protected data; Task 6 implements the selected workbench; Task 7 proves security, responsive behavior and Hallmark quality.
- Placeholder scan: no TODO, TBD, or deferred implementation instruction appears in this plan.
- Type consistency: Task 1 supplies contracts, Task 2 durable records, Task 3 auth, Task 4 storage/TTS, Task 5 APIs, and Task 6 frontend consumers.

