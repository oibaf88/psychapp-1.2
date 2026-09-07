# PsychDeep local/offline architecture

PsychDeep can run as a local/offline copy of the hosted application while keeping GitHub `master` as the code source of truth and synchronizing an explicit allowlist of PostgreSQL application data with Supabase.

## Components

- PostgreSQL 17 local (`127.0.0.1:5433`) with the same `psychdeep_v12` schema name used remotely.
- PsychDeep backend local (`127.0.0.1:8001`) and frontend local (`127.0.0.1:5173`).
- LM Studio on Windows (`localhost:1234`) as an OpenAI-compatible local inference endpoint.
- SymmetricDS 3.18.0 under the Compose `sync` profile for queued bidirectional data synchronization after connectivity returns.
- A remotely-managed Cloudflare Tunnel under the Compose `tunnel` profile when the hosted Render backend must reach LM Studio on the laptop.

## Source of truth

The hosted GitHub repository is authoritative for code and schema migrations. The local working tree should track the same `master` commit; local configuration, database volumes, generated engine files, model weights and secrets are deliberately outside Git.

Schema changes flow through Git/Supabase migrations. SymmetricDS synchronizes data rows only; it is not a schema-migration system.

## Normal offline flow

```text
Browser -> PsychDeep frontend local -> backend local -> PostgreSQL 17 local
                                      -> LM Studio local
```

Once model weights are present locally, this path needs no Internet connection.

```powershell
Copy-Item .env.local.example .env.local
# Set LOCAL_DB_PASSWORD and JWT_SECRET.
.\ops\local\start-local.ps1
```

Open `http://127.0.0.1:5173`.

## LM Studio from the Docker backend

The backend container reaches Windows through `host.docker.internal`.

1. In LM Studio enable API-token authentication.
2. Enable serving on the local network so Docker Desktop can reach the host server.
3. Keep Windows Firewall enabled; do not expose port 1234 on public networks.
4. Start the OpenAI-compatible server on port 1234.
5. For the local PsychDeep instance use:

```text
Base URL: http://host.docker.internal:1234/v1
API key:  <LM Studio API token>
```

## Remote model flow

```text
Render backend -> HTTPS stable hostname -> Cloudflare Tunnel -> LM Studio :1234
```

Only the model endpoint is tunneled. PostgreSQL is never routed through Cloudflare Tunnel. Use a remotely-managed named tunnel with a fixed hostname and keep LM Studio API-token authentication enabled. `ops/local/secrets/cloudflare-tunnel-token.txt` and every other generated secret are ignored by Git.

Production has `LLM_ALLOW_RUNTIME_OVERRIDE=true`, but this does **not** switch away from Claude by itself. With no active `llm_endpoint_configs` row, the environment's Anthropic configuration remains in force. Only an authenticated `admin_clinical` account can test/save/reset a runtime endpoint, and every change is audited. Render also rejects private/LAN targets and non-HTTPS model URLs. This lets the operator switch to the authenticated Cloudflare hostname without redeploying while preserving Claude as the default/fallback.

See `docs/LOCAL_MODEL_TUNNEL.md`.

## Database synchronization

Data flow is bidirectional but network initiation is outbound from the laptop:

```text
local changes:  laptop --push--> cloud
cloud changes:  laptop <--pull-- cloud
```

When offline, changes remain queued locally. When the connection returns, SymmetricDS resumes delivery.

### Cloud connection

`configure-sync.ps1` prefers the direct Supabase PostgreSQL endpoint on port 5432. If the Windows network cannot reach the project's IPv6 direct endpoint, it falls back to Supavisor **session mode** on port 5432. Transaction-pooling port 6543 is rejected because a persistent DDL/trigger-based replication engine must retain session semantics.

The cloud engine authenticates only as `psychdeep_sync`, never as `postgres` or `psychdeep_backend`.

### Replicated allowlist

`ops/sync/config/psychdeep-sync.sql` contains the fixed 19-table allowlist:

- `users`
- `user_consents`
- `patient_professional_assignments`
- `confirmed_facts`
- `baselines`
- `check_ins`
- `diary_entries`
- `safety_plans`
- `chat_messages`
- `patient_profiles`
- `agent2_analysis_traces`
- `alfa_signals`
- `psychosocial_observations`
- `risk_assessments`
- `professional_alerts`
- `notifications`
- `therapist_copilot_messages`
- `audit_log`
- `llm_usage_events`

Deliberately excluded:

- `llm_endpoint_configs`: local and cloud use different inference endpoints/configuration.
- `password_reset_tokens`: authentication/reset secrets are node-local.
- every `psychdeep_sync.sym_*` table: SymmetricDS owns its runtime metadata.
- the quarantined pre-v12 schema `psychdeep_legacy_20260809`.

Incoming replication batches are not re-captured (`sync_on_incoming_batch=0`), preventing loops.

## First enablement

The Supabase migrations create a least-privilege `psychdeep_sync` role and the private `psychdeep_sync` runtime schema. The role has no owner/admin privileges, no RLS bypass, no TRUNCATE, and no access to the two excluded tables. Its LOGIN password is an operational secret and is never stored in Git.

After the local secret has been placed on the PC:

```powershell
.\ops\sync\configure-sync.ps1
.\ops\sync\start-sync.ps1 -Initialize
```

`start-sync.ps1 -Initialize` now performs the rollout sequence automatically:

1. checks the Supabase least-privilege preconditions;
2. starts the SymmetricDS cloud/local engines;
3. waits for `psychdeep_sync.sym_*` runtime tables to appear;
4. applies the fixed 19-table routing/trigger configuration;
5. opens one registration window for the laptop node;
6. restarts the engine so initial synchronization begins;
7. checks cloud node-registration metadata.

No clinical record is automatically created as a test. Validate both directions with a disposable non-clinical account/row before permitting simultaneous editing.

## Conflict model

PostgreSQL tables and resources should remain structurally equivalent, but local and cloud are not byte-for-byte clones of the entire Supabase project. Supabase-managed Auth/Storage/Realtime internals, runtime model configuration, reset tokens and SymmetricDS metadata are intentionally node-specific.

The 19 replicated application tables may be multi-writer, but avoid concurrent edits to the same logical record until the conflict rules have been validated for that table. Append-oriented records are naturally safer than mutable derived clinical state.

## Security invariants

- PostgreSQL, backend, frontend and SymmetricDS management ports bind to `127.0.0.1` on the laptop.
- Production permits runtime endpoint selection only for `admin_clinical`; Claude remains the default/fallback until an active, reachable HTTPS override is deliberately saved.
- `psychdeep_sync` is NOINHERIT, not superuser, has no BYPASSRLS and is connection-limited.
- The sync role has CRUD + TRIGGER only for the allowlist and no TRUNCATE.
- `llm_endpoint_configs` and `password_reset_tokens` are not readable by the sync role.
- Cloudflare Tunnel exposes only the authenticated model endpoint, never PostgreSQL.
- `.env.local`, engine property files, database passwords, LM Studio tokens and Cloudflare tokens are never committed.
- The local LLM usage ledger exists so local inference remains auditable.

## Stop safely

```powershell
.\ops\local\stop-local.ps1
```

This stops containers but preserves PostgreSQL volumes. Do not use `docker compose down -v` unless you intentionally want to erase the offline database.
