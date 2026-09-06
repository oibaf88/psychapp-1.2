# PsychDeep local/offline architecture

This is an **opt-in** deployment mode. Production remains unchanged unless an operator explicitly enables synchronization or a tunnel.

## Components

- PostgreSQL 17 local (`127.0.0.1:5433`) with the same `psychdeep_v12` application schema name used remotely.
- PsychDeep backend local (`127.0.0.1:8001`) and frontend local (`127.0.0.1:5173`).
- LM Studio on Windows (`localhost:1234`) as an optional fully local OpenAI-compatible LLM endpoint.
- SymmetricDS 3.18.0 under the Compose `sync` profile for queued, bidirectional PostgreSQL replication after connectivity returns.
- Cloudflare Tunnel under the Compose `tunnel` profile **only** when the hosted Render backend must reach the laptop's LM Studio server.

## Normal offline flow

```text
Browser -> PsychDeep frontend local -> backend local -> PostgreSQL 17 local
                                      -> LM Studio local
```

No Internet is required when a local model is selected. Start with:

```powershell
Copy-Item .env.local.example .env.local
# edit LOCAL_DB_PASSWORD and JWT_SECRET
.\ops\local\start-local.ps1
```

Open `http://127.0.0.1:5173`.

If LM Studio is running on Windows, configure the OpenAI-compatible endpoint in PsychDeep Settings as:

```text
http://host.docker.internal:1234/v1
```

## Tunnel flow - model only

```text
Render backend -> HTTPS hostname -> Cloudflare -> cloudflared (outbound tunnel)
                                              -> LM Studio :1234
```

The database is **never** routed through Cloudflare Tunnel. Enable LM Studio API-token authentication before publishing its endpoint. Store the Cloudflare tunnel token in `ops/local/secrets/cloudflare-tunnel-token.txt` via `start-tunnel.ps1`; the directory is git-ignored.

## Database synchronization

SymmetricDS uses a deliberately asymmetric network pattern even though the data synchronization is bidirectional:

```text
local changes:  laptop --push--> cloud
cloud changes:  laptop <--pull-- cloud
```

The laptop initiates all network connections. When it is offline, changes stay queued. When connectivity returns, SymmetricDS resumes delivery.

### Replicated allowlist

The allowlist is defined in `ops/sync/config/psychdeep-sync.sql`. It includes application/clinical rows and lineage/audit data needed to reconstruct the same patient state on both ends.

Deliberately excluded:

- `llm_endpoint_configs`: local and cloud are expected to use different inference endpoints.
- `password_reset_tokens`: authentication secrets/tokens are not portable application state.
- every `psychdeep_sync.sym_*` table: SymmetricDS owns its own runtime metadata.

Incoming batches are not re-captured (`sync_on_incoming_batch=0`), preventing replication loops.

### First enablement

Do **not** enable synchronization for the first time directly against live clinical data without a backup and a staging test.

1. Start local PsychDeep and verify it works independently.
2. Run `.\ops\sync\configure-sync.ps1` to generate the two secret engine property files.
3. Back up Supabase; preferably reproduce the remote database in staging first.
4. Run `.\ops\sync\start-sync.ps1 -Initialize`.
5. After the cloud engine creates `psychdeep_sync.sym_*`, apply `ops/sync/config/psychdeep-sync.sql` using DBeaver/psql to the cloud database.
6. Open registration for the local node using the command printed by the script.
7. Restart the SymmetricDS container and verify a non-clinical test row in both directions.
8. During the first rollout, do not actively edit the same patient's record from local and hosted PsychDeep at the same time. Conflict policy should be validated before true multi-writer use.

## Security invariants

- PostgreSQL, backend, frontend and SymmetricDS management ports bind to `127.0.0.1`, not `0.0.0.0`.
- Production keeps `LLM_ALLOW_RUNTIME_OVERRIDE=false`; the local `.env.local` explicitly opts into it.
- The Cloudflare tunnel exposes only the model endpoint and requires LM Studio API-token authentication.
- Generated SymmetricDS engine files and Cloudflare tokens are not committed.
- The local LLM usage ledger is created at database bootstrap so local inference remains auditable too.

## Stop safely

```powershell
.\ops\local\stop-local.ps1
```

This stops containers but preserves the PostgreSQL volume. Do not use `docker compose down -v` unless you intentionally want to erase the offline database.
