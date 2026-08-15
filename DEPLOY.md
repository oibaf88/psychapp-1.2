# Deploying PsychDeep 1.2 (Render + Supabase + Claude)

Target: **https://psychapp.bfab.io**

Architecture:

| Piece | Runs on | Notes |
|---|---|---|
| Frontend (React/Vite) | Render **static site** | `psychdeep-web` |
| Backend (FastAPI) | Render **web service** (Docker) | `psychdeep-api` |
| Database | **Supabase** Postgres (`psychdeep`, eu-north-1) | project ref `ifwexmoltnybvmrsuwtu` |
| Agent 1 — conversation | **Anthropic API** | `ANTHROPIC_CHAT_MODEL` |
| Agent 2 — linguistic analysis | **Anthropic API** | `ANTHROPIC_ANALYSIS_MODEL` |

Both agents run on the Claude API. There is no local/offline model path
any more — the OpenAI-compatible provider was removed.

---

## 0. The leaked API key — resolved

A real Anthropic API key was once committed to `.env.example` and is still
present in this repository's git history (commit `b6059d0` onwards).

**It was revoked and replaced within about 30 seconds of being published,
and the replacement has never been committed.** Nothing further is needed.

The dead key remains visible in history; that is harmless now, but it is
the reason keys live only in Render's environment settings and never in a
tracked file.

---

## 1. Supabase

The `psychdeep` project already exists and is `ACTIVE_HEALTHY`. The
production backend does **not** run SQLAlchemy `create_all()`: schema changes
must be applied explicitly before a Render release. This prevents the API from
creating an unhashed/unprotected trace table or starting against half-migrated
columns.

For the risk-explanation/Agent 2 tracking release, apply
[`20260815120000_add_risk_explanations_agent2_tracking.sql`](./supabase/migrations/20260815120000_add_risk_explanations_agent2_tracking.sql)
to project `ifwexmoltnybvmrsuwtu` **before merging/deploying the application**.
It is an expand-only transaction: existing rows remain valid, the new trace
table is owned by `psychdeep_backend`, FORCE RLS is enabled, and `public`,
`anon`, `authenticated` and `service_role` receive no table privileges. The
temporary role membership needed to alter the backend-owned tables is verified,
scoped to the transaction and removed before commit.

For the therapist-panel redesign release, also apply
[`20260815160000_add_therapist_copilot_messages.sql`](./supabase/migrations/20260815160000_add_therapist_copilot_messages.sql).
It adds the single new table behind the clinical copilot
(`therapist_copilot_messages`) with the same hardening: owned by
`psychdeep_backend`, FORCE RLS on, a `backend_full_access` policy, and no
privileges for `public`, `anon`, `authenticated` or `service_role`. Everything
else in that release — the explanations, the metric series, the evidence feed
and the patient chat transcript — is computed from existing tables and needs no
schema change.

For the psychosocial-context release, also apply
[`20260815180000_add_psychosocial_observations.sql`](./supabase/migrations/20260815180000_add_psychosocial_observations.sql).
It adds `agent2_analysis_traces.agent_role` (backfilled to
`agent2_linguistic`, which is what existing rows are) and the
`psychosocial_observations` table, hardened the same way. That table stores a
bounded verbatim fragment of the patient's own text in `evidence_quote`, so it
is at least as sensitive as `chat_messages` and is protected identically.

The API validates the required columns, owner, FORCE RLS and backend policy at
startup and fails closed if any migration is missing (the startup check covers
`therapist_copilot_messages`, `agent2_analysis_traces.agent_role` and
`psychosocial_observations`). Local/dev environments may still use
`create_all()` for disposable databases.

> **Order matters.** Apply the migration *before* the Render deploy. The new
> release fails its startup schema check without it, and Render will keep
> serving the previous instance rather than a half-migrated one.

Already applied for you — migration
`lock_public_schema_from_postgrest_roles`: revokes the default privileges
Supabase would otherwise grant `anon` and `authenticated` on new tables
in `public`. Without it, anyone holding the publishable anon key could
read every patient record over PostgREST.

**Get the connection string**: Supabase Dashboard → *Connect* → copy the
**connection pooler** URI (Session mode).

> Use the pooler host, not `db.<ref>.supabase.co`. The direct host is
> IPv6-only and Render cannot reach it.

Shape actually in use:

```
postgresql+psycopg2://USER:PASSWORD@aws-0-eu-north-1.pooler.supabase.com:5432/postgres?sslmode=require&options=-csearch_path%3Dpsychdeep_v12
```

Three parts matter:

| Part | Why |
|---|---|
| `postgresql+psycopg2://` | Names the driver explicitly. Plain `postgresql://` also works — SQLAlchemy defaults to psycopg2 — but being explicit avoids surprises if the driver ever changes. |
| `sslmode=require` | Supabase requires TLS. |
| `options=-csearch_path%3D<schema>` | Puts the tables in a dedicated schema instead of `public`. `%3D` is the URL-encoded `=`. |

**Use a dedicated schema.** Supabase exposes `public` through PostgREST, so
tables created there are reachable with the publishable anon key unless
locked down. A schema like `psychdeep_v12` is not exposed at all, which is
a stronger and simpler guarantee.

**The schema must exist before the first boot** — `create_all()` creates
tables, not schemas:

```sql
create schema if not exists psychdeep_v12;
```

`DATABASE_SCHEMA=psychdeep_v12` is an equivalent alternative to the
`options=` parameter. Set one or the other, not both.

---

## 2. Render

Render Dashboard → **New → Blueprint** → select this repository. It reads
[`render.yaml`](./render.yaml) and creates both services.

> **What a blueprint is.** `render.yaml` is infrastructure-as-code: it
> declares both services (runtime, region, build command, health check,
> plan, non-secret environment variables) so they are created identically
> every time instead of being clicked together by hand. It is also the
> record of *why* the services are configured that way. You can ignore it
> and create the two services manually — the blueprint just saves the
> clicking and keeps the config in git.

### If Render did not prompt for the secrets

Depending on the flow, Render may create the services without asking for
the `sync: false` values, leaving them unset. The API will then fail to
start (no `DATABASE_URL`) and the frontend will build against nothing.

Set them per service, in the dashboard:

**`psychdeep-api`** → *Environment* → **Add Environment Variable**:

| Key | Value |
|---|---|
| `DATABASE_URL` | the Supabase pooler URI from step 1 |
| `ANTHROPIC_API_KEY` | your current key |

**`psychdeep-web`** → *Environment*:

| Key | Value |
|---|---|
| `VITE_API_BASE_URL` | the `psychdeep-api` URL, e.g. `https://psychdeep-api.onrender.com` |

Saving triggers a redeploy of the API. The frontend needs an explicit
**Manual Deploy → Clear build cache & deploy**, because `VITE_API_BASE_URL`
is baked in at build time — a restart will not pick it up.

Everything not marked `sync: false` (models, effort levels, `APP_ENV`,
`SEED_DEMO_DATA=false`, `CORS_ORIGINS`) comes from `render.yaml` and is
already set. `JWT_SECRET` is generated by Render.

### Reference: the values Render would have asked for

| Service | Variable | Value |
|---|---|---|
| `psychdeep-api` | `DATABASE_URL` | the Supabase pooler URI from step 1 |
| `psychdeep-api` | `ANTHROPIC_API_KEY` | the **new** key from step 0 |
| `psychdeep-api` | `SMTP_*` | optional; alerts work without it |
| `psychdeep-web` | `VITE_API_BASE_URL` | the API URL, e.g. `https://psychdeep-api.onrender.com` |

`JWT_SECRET` is generated by Render. `SEED_DEMO_DATA` is pinned to
`false` — the four demo accounts have well-known passwords and must never
exist in a public deployment.

`VITE_API_BASE_URL` is baked in at **build time**. Changing it requires a
redeploy of the static site, not just a restart.

### Domain

Point `psychapp.bfab.io` at **`psychdeep-web`** (Render → service →
Settings → Custom Domains, then the CNAME it gives you).

If you also give the API a custom domain, update `CORS_ORIGINS` on
`psychdeep-api` to match the frontend origin exactly — in
`APP_ENV=production` the API does not fall back to permissive origin
regexes.

---

## 3. After the first successful deploy

1. Check `https://<api-url>/api/v1/health`. Expected:

   ```json
   {
     "status": "ok",
     "llm_configured": true,
     "llm_provider": "anthropic",
     "chat_model": "claude-opus-5",
     "analysis_model": "claude-opus-5",
     "risk_engine_version": "risk-engine-v1.2",
     "risk_explanation_schema": "risk-explanation-v1",
     "agent2_tracking": true,
     "release": "<deployed-git-sha>"
   }
   ```

2. Verify both Claude agents, from the `psychdeep-api` service's
   **Shell** tab:

   ```bash
   python scripts/smoke_llm.py
   ```

   It calls Agent 1 and Agent 2 once each and prints what came back,
   exiting non-zero if either fails. No database writes, no seeded data.

   Agent 2 fails safely at runtime — the deterministic engine continues and
   the attempt remains visible with a sanitized status in the clinical tracking
   screen. The smoke script confirms the provider/model independently.

   > Locally the equivalent is `docker compose exec backend python
   > scripts/smoke_llm.py`, run **from the project root**. Run from
   > anywhere else and Docker reports `no configuration file provided:
   > not found`, because it looks for `docker-compose.yml` in the current
   > directory.

3. Run [`supabase/harden.sql`](./supabase/harden.sql) (Supabase → SQL
   Editor).

   **Edit `TARGET_SCHEMA` at the top first** so it matches the schema in
   your `DATABASE_URL` (e.g. `psychdeep_v12`). Pointed at the wrong
   schema, the script reports success against an empty one — `Success. No
   rows returned` against `public` means it found nothing, not that
   everything is fine.

   The final `select` scans **every** schema for exactly this reason.
   Each application table should show `rls_enabled = true` and both
   `*_can_select` columns `false`.

4. With synthetic accounts, verify the clinical UI as both an assigned
   therapist and a supervisor:

   - **Motor de riesgo** shows all 11 evaluated rules, the selected rule,
     formulas, baseline/recent values, z-scores, thresholds, sleep slope,
     persistence dates and the stored conclusion.
   - **Agent 2** shows the exact source text, validated JSON response, source /
     signal / correlation / assessment identifiers, whether the signal was
     actually consumed, model/provider metadata, tokens, latency and sanitized
     failure fields.
   - An unassigned therapist, a patient and `admin_clinical` receive `403` for
     the clinical trace endpoints.

5. Confirm `agent2_analysis_traces` has `relrowsecurity=true`,
   `relforcerowsecurity=true`, owner `psychdeep_backend`, one
   `backend_full_access` policy, and no privileges for PostgREST roles. Remove
   every synthetic row/account used by the smoke test after verification.

---

## Notes and trade-offs

**Render plan.** `render.yaml` pins both services to `free` so nothing is
billed without you choosing it. The free web service spins down after
~15 minutes idle and cold-starts in roughly a minute; the backend's
database wait-loop makes that noticeable. Switch `plan: free` to
`plan: starter` on `psychdeep-api` for an always-on instance.

**Model choice and cost.** Both agents default to `claude-opus-5`. Agent 2
runs at `high` effort because its output drives alert levels; Agent 1 runs
at `medium`. Every model and effort level is an environment variable, so
you can tune cost without a code change. Lower Agent 2's effort only
against your own evaluation set — it is the safety-critical path.

**Refusals.** Claude's safety classifiers can decline a request; the
provider raises `RefusalError` in that case. The crisis flow already
returns the server-owned safety templates whenever the LLM call raises,
so a refusal degrades to the same safe output as a network error — the
crisis path never depends on the model succeeding.

**Static site cannot proxy `/api`.** The Docker Compose setup uses nginx
to proxy `/api` to the backend on the same origin. A Render static site
cannot do that, which is why the frontend calls the API cross-origin via
`VITE_API_BASE_URL` and the backend allows it via `CORS_ORIGINS`.
