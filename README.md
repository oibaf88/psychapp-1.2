# PsychApp

A locally-run self-regulation and risk-monitoring app for people navigating stimulant
use / chemsex patterns and associated suicide risk (Madrid/Spain context), built from
~20 spec/documentation PDFs provided for this project. It pairs a **deterministic,
fully local risk engine** with a **Claude-powered two-agent LLM layer** for
conversation and free-text linguistic analysis.

This README covers what was built, how it maps to the source docs, how to run it,
and — importantly — everywhere this implementation had to fill a gap or deviate from
the docs, so you can course-correct anything that matters to you.

---

## 1. What this actually is

The spec docs describe a support tool for people managing stimulant use disorder
and/or chemsex patterns, with an elevated suicide-risk population, used in a
Madrid/Spain clinical context, in Spanish. It is **not** a generic "psych app" —
that was just the working filename. Core ideas from the docs:

- A **patient app**: daily check-ins, a free-text diary, an AI chat companion, a
  "wave" screen for urge-surfing / grounding techniques during craving or distress,
  and a personal safety plan.
- A **professional app**: therapists/supervisors/clinical admins see their assigned
  patients, a longitudinal timeline of facts and signals, and get alerted when a
  patient's risk escalates.
- A **strict separation between facts and inferences**: things a person or clinician
  has explicitly stated (`ConfirmedFact`) are never silently overwritten by anything
  the system infers from behavior or language (`AlfaSignal`).
- A **deterministic risk engine** (not an LLM) that turns recent signals and facts
  into an `alert_level` 0–4 through an explicit, auditable rule cascade — because
  risk classification in a system like this must be traceable and reproducible, not
  a black box.
- A **two-agent LLM architecture**: one agent talks to the patient (empathetic,
  grounded in MI/CBT/DBT-STOP/urge-surfing techniques, always redirecting to crisis
  resources when needed, and explicitly never allowed to compute or state a risk
  level itself); a second, separate agent only ever reads free text the user wrote
  and returns structured data about it — it never talks to the user.
- **Server-owned, static Spanish crisis messaging** for Level 3/4 escalations —
  these are never LLM-generated, so a crisis response never depends on API
  availability or model behavior. They include Línea 024, 112, and Madrid-specific
  chemsex resources (Sandoval, Red CAD, Apoyo Positivo, Imagina MÁS, COGAM, Energy
  Control).
- Certain grounding techniques (ice cubes, rubber-band snapping — "TIPP" cold/pain
  methods) are **explicitly excluded** throughout the docs and this implementation,
  replaced with safer alternatives (textured objects, feet-on-floor grounding,
  citrus/mint taste, physiological sigh breathing).

## 2. Tech stack

- **Backend**: FastAPI (Python 3.11), SQLAlchemy 2.0, PostgreSQL 16, JWT auth
  (python-jose) with `bcrypt` used directly for password hashing.
- **Frontend**: React 18 + TypeScript + Vite, Recharts for timeline charts.
- **LLM**: Anthropic Python SDK, calling Claude via the Messages API. Agent 2
  (linguistic analysis) uses forced tool-use so its output is always
  well-formed JSON, never free text.
- **Orchestration**: Docker Compose (db + backend + frontend). No Kubernetes,
  no cloud dependency beyond the Anthropic API call itself.

### Why Claude via API, not a fully offline model

The docs (in an earlier iteration) explored running local/fine-tuned open models.
Per the explicit brief for this build, Claude does not distribute downloadable
model weights, so there is no way to run "the Claude model" fully offline — the
only real integration is a local server calling the Anthropic API over the network.
Everything else in this app (database, the risk engine, the UI, all patient/clinical
data) runs entirely on your machine; only the chat and linguistic-analysis calls
leave it, going to Anthropic's API. The LLM call is behind a small `LLMProvider`
interface (`backend/app/services/llm/`) specifically so a future local-model
provider could be swapped in without touching the rest of the app.

## 3. How it maps to the spec docs

| Area | What's implemented | Source material |
|---|---|---|
| Fact vs. inference model | `ConfirmedFact` / `AlfaSignal` tables, facts immutable except via versioned correction | "Muro de Hechos vs Inferencias" docs |
| Deterministic risk engine | `app/services/risk_engine.py` — pure-Python rule cascade producing `alert_level` 0–4, fully traceable (`triggering_rules`, `input_signals`, `input_facts`, `model_version`) | risk-engine pseudocode/schema docs |
| Two-agent LLM architecture | Agent 1 (`AGENT1_SYSTEM_PROMPT`, conversational, MI/CBT/DBT-STOP, never computes risk) + Agent 2 (`AGENT2_SYSTEM_PROMPT` + forced-tool-use `AGENT2_TOOL_SCHEMA`, structured-only, never talks to the user) | final two-agent architecture summary docs |
| Escalation messaging | Static, server-owned Spanish templates for Level 3 (professional alarm) and Level 4 (emergency/crisis), in `app/content/safety_resources.py` | escalation-copy docs |
| Local structural baseline | Rolling Z-score `structural_score` (1.0 = matches personal baseline → 0 = severe deviation) + `confidence_band` (stable/transition/unstable), computed locally in `app/services/baseline.py` | see **Assumption (a)** below — this deliberately replaces a third-party service mentioned in one doc |
| RBAC | Role-scoped permissions (signal visibility, fact visibility, alert/assignment management, audit log access) for `therapist` / `supervisor` / `admin_clinical` in `security.py` + router-level dependencies | RBAC matrix doc |
| Patient–professional assignment lifecycle | `PatientProfessionalAssignment` with pending/active/paused/ended/rejected states, consent-gated | assignment lifecycle doc |
| Safety plan | `SafetyPlanPage` + `SafetyPlan` model — patient-authored plan with template prompts | safety-plan doc |
| Urge-surfing / grounding ("Wave") | `WavePage` — breathing pacer, DBT STOP prompts, TIPP-safe grounding alternatives only | urge-surfing / TIPP-exclusion docs |
| Audit log | `AuditLog` model + `app/services/audit.py`, queried via `admin_clinical`-only router | audit-log doc |

## 4. Setup

### Prerequisites

- Docker and Docker Compose (Docker Desktop on Mac/Windows, or `docker` + the
  `compose` plugin on Linux).
- An Anthropic API key from <https://console.anthropic.com/>. Chat and diary
  linguistic analysis will not work without one; everything else (check-ins,
  timeline, safety plan, auth, professional dashboard) works fine without it.

### Steps

1. Copy the environment template and fill in your key:

   ```bash
   cp .env.example .env
   ```

   Open `.env` and set:

   ```
   ANTHROPIC_API_KEY=sk-ant-...your key...
   ```

   Also replace `JWT_SECRET` with a real random value for anything beyond local
   experimentation:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

2. Start everything:

   ```bash
   docker compose up --build
   ```

   or use the convenience launcher, which does step 1 for you if `.env` doesn't
   exist yet and then runs compose:

   - macOS/Linux: `./start.sh`
   - Windows PowerShell: `./start.ps1`

3. Once containers are healthy:
   - Frontend: <http://localhost:5173>
   - Backend API + docs: <http://localhost:8000/docs>
   - Health check: <http://localhost:8000/api/v1/health>

4. On first startup (`SEED_DEMO_DATA=true`, the default), the backend seeds four
   demo accounts and ~21 days of synthetic check-in history so the app is
   explorable immediately:

   | Role | Email | Password |
   |---|---|---|
   | Patient | `patient@demo.psychapp.example.com` | `DemoPass123!` |
   | Therapist | `therapist@demo.psychapp.example.com` | `DemoPass123!` |
   | Supervisor | `supervisor@demo.psychapp.example.com` | `DemoPass123!` |
   | Clinical admin | `admin@demo.psychapp.example.com` | `DemoPass123!` |

   Set `SEED_DEMO_DATA=false` in `.env` once you have real data you don't want
   touched by the seeder (it's idempotent — safe to leave on for repeated local
   restarts, but turn it off before anything resembling production use).

To stop: `Ctrl+C`, then `docker compose down` (add `-v` to also drop the Postgres
volume and start clean next time).

### Running the backend outside Docker (optional)

If you want to run the API directly against a local Postgres instead of the
`db` container: point `DATABASE_URL` in `.env` at your Postgres instance, then
from `backend/`: `pip install -r requirements.txt && uvicorn app.main:app --reload`.

## 5. Verification status — please read

**I was not able to execute or run this application in this session.** The
sandboxed shell environment this session runs in failed to start for the entire
session (a persistent VM-connection timeout on every retry, including a final
retry made specifically to test this before writing this section) — so I could not
run `docker compose up`, run the backend, hit the API, or click through the UI.
Everything above was built and then reviewed by hand, file by file, but **it has
not been machine-verified**, and you should treat it as untested code that needs a
real first run, not as something confirmed working.

To be concrete about what "hand-reviewed" means: I re-read every backend module
for import correctness, model/schema field consistency, and correct SQLAlchemy /
Pydantic v2 / FastAPI usage, and re-checked the Anthropic SDK call pattern against
its known API shape. In that process I found and fixed several real bugs (see
§7 below) — which is exactly the kind of thing an actual run plus a smoke test
would normally catch faster and more completely. I'd genuinely expect at least
one more issue to surface on first boot (missing import, a wrong SQL type, a
frontend/backend contract mismatch) that a static read-through can miss.

**Please run this yourself before trusting it with anything real**, and tell me
what breaks — I can fix it fast once I can see an actual error. A reasonable
first pass:

1. `docker compose up --build` and confirm all three containers report healthy
   / stay up (watch `backend` logs especially — that's where a startup crash
   would show).
2. `curl http://localhost:8000/api/v1/health` → expect `200 OK`.
3. Log in as `patient@demo.psychapp.local` at <http://localhost:5173>, confirm
   the dashboard loads with the seeded 21 days of check-in history.
4. Submit a new check-in, write a diary entry, and try the chat (this last one
   needs `ANTHROPIC_API_KEY` set) — confirm Agent 1 responds and nothing 500s.
5. Log in as `therapist@demo.psychapp.local`, confirm the assigned demo patient
   shows up with a timeline and no alert-visibility errors.
6. If you can, deliberately trigger a Level 3/4 scenario (e.g. via repeated
   concerning check-ins/diary content) and confirm the static Spanish crisis
   copy renders — this is the single most safety-critical path in the app and
   the one most worth verifying by hand regardless of what automated tests say.

## 6. Assumptions and gaps

The docs were thorough in places and thin or ambiguous in others, and a few were
internally inconsistent with each other (later docs refining or overriding
earlier ones). Where I had to make a call, here's every one of them:

**(a) Local statistics instead of the third-party "AlphaInfo.io" service.**
One of the docs described piping behavioral data through a third-party service
(package + API key) to compute the structural/baseline score. I did not integrate
it. It reads like a pasted AI-assistant chat transcript rather than a formal
requirement, it isn't consistent with the privacy-by-design principles stated
elsewhere in the docs, and sending sensitive mental-health data to an unverified
third party is a real risk I wasn't willing to build in without being asked
explicitly. Instead, `app/services/baseline.py` computes an equivalent rolling
Z-score-based `structural_score` and `confidence_band` entirely locally, from
data already in your own database. If you did specifically want the third-party
integration, that's a deliberate deviation you should know about and can push
back on.

**(b) Simplified auth instead of the full passkey/WebAuthn/KMS vision.**
One doc sketched a more sophisticated auth model (passkeys, WebAuthn, KMS-managed
secrets). This build uses standard JWT bearer tokens with `bcrypt`-hashed
passwords — solid for a local/dev deployment, but not what that doc envisioned
for a production clinical rollout.

**(c) No SMS/push notifications.** Professional alerts create in-app notifications
always, and best-effort email if you configure SMTP in `.env`. Twilio (SMS) and
FCM/APNs (push) integrations described in the docs are not implemented.

**(d) Supervisor role sees all patients, not "their team."** The docs implied
some notion of team-scoped visibility for supervisors; there's no team/org
modeling in this build, so `supervisor` currently has the same patient visibility
as `admin_clinical` minus admin-only actions (audit log, assignment overrides).

**(e) `structural_score` persistence-band counts recent readings, not distinct
calendar days.** The risk-engine docs implicitly assume one reading per day when
computing how "persistent" a deviation is. In this implementation,
`compute_structural_score()` is recomputed on every check-in, diary entry, and
chat message — so if a patient logs multiple times in one day, the persistence
band counts recent *rows*, not distinct *days*. Worth tightening if the exact
day-based semantics matter to you; flagged clearly in code comments in
`risk_engine.py`.

**(f) No formal migrations.** Tables are created via
`Base.metadata.create_all()` on startup rather than Alembic migrations. Fine for
a fresh local deployment; if you evolve the schema later you'll want to either
add Alembic or handle migrations by hand.

**(g) Both LLM agents run on Claude, not separate fine-tuned open models.** An
earlier doc explored fine-tuning distinct open models per agent. Per the explicit
brief for this build, both Agent 1 (conversation) and Agent 2 (linguistic
analysis) call Claude via the Anthropic API, distinguished only by system prompt
and (for Agent 2) forced tool-use schema. The `LLMProvider` abstraction in
`app/services/llm/` is the intended seam if you want to swap in something else
later (including a self-hosted model) without touching the rest of the app.

**(h) MVP scope stops at Level A/B, deliberately excludes Level C/D.** One doc
explicitly recommended *not* building clinical-prediction / medical-device-territory
features (e.g. anything that could read as a diagnostic or predictive clinical
claim) in an initial version, and that recommendation is followed here — this
app supports and informs, it does not diagnose or predict in a clinical sense.

**(i) Spanish-only, Madrid-specific resource content.** The crisis resources and
UI copy are in Spanish and reference Madrid-specific services (Sandoval, Red CAD,
etc.) per the docs' explicit population and geography. Nothing here is localized
for other regions or languages.

## 7. Notable bugs found and fixed during review

Since I couldn't run automated tests, I did a manual pass through the codebase
looking for exactly these kinds of issues, and want to be upfront about what
I found rather than imply everything was clean on the first try:

- `passlib` + modern `bcrypt` (>=4.1) have a known incompatibility (passlib
  probes for a removed internal attribute). Rewrote `security.py` to call
  `bcrypt` directly instead of going through `passlib`.
- Several router path parameters (in `assignments.py`, `facts.py`,
  `notifications.py`) were untyped `str` where they should have been
  `uuid.UUID`, which would likely have caused type mismatches against
  UUID-typed database columns. Fixed.
- `AssignmentRequestIn.professional_email` was actually supposed to be the
  *patient's* email a professional requests access to — misnamed in a way that
  would have confused anyone reading or calling that endpoint. Renamed to
  `patient_email`.
- `consents.py` had a validation check that was structured as a ternary but
  never actually raised on invalid input. Fixed to a proper guard.
- `NotificationOut.id`, `AuditLogOut.id`, and `AuditLogOut.actor_id` were typed
  as `str` instead of `uuid.UUID` in the Pydantic schemas, which would likely
  fail `from_attributes` validation against real ORM UUID objects. Fixed.
- The frontend's `VITE_API_BASE_URL` was originally set as a plain container
  `environment:` variable in `docker-compose.yml` — but Vite bakes
  `import.meta.env.VITE_*` values in at *build* time, so a runtime environment
  variable has no effect on the built static bundle. Moved it to Docker
  `build.args` and threaded it through `frontend/Dockerfile` (`ARG` /
  `ENV` before `RUN npm run build`) so it actually takes effect.

I'd treat this list as evidence that a real run will very likely surface at
least one more thing — please run it and let me know what you hit.

## 8. Project layout

```
psychapp/
├── docker-compose.yml
├── .env.example
├── start.sh / start.ps1
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, CORS, startup seeding
│   │   ├── config.py          # env-driven settings
│   │   ├── database.py
│   │   ├── models.py          # SQLAlchemy models (13 tables)
│   │   ├── schemas.py         # Pydantic request/response models
│   │   ├── security.py        # JWT auth + bcrypt hashing
│   │   ├── seed.py            # idempotent demo data
│   │   ├── content/
│   │   │   ├── prompts.py         # Agent 1 / Agent 2 system prompts + tool schema
│   │   │   └── safety_resources.py # static Spanish crisis copy + resources
│   │   ├── services/
│   │   │   ├── llm/                # swappable LLM provider (Anthropic implementation)
│   │   │   ├── baseline.py         # local structural_score / confidence_band
│   │   │   ├── risk_engine.py      # deterministic alert_level cascade
│   │   │   ├── conversation.py     # Agent2 -> risk_engine -> Agent1 orchestration
│   │   │   ├── notifications.py
│   │   │   ├── audit.py
│   │   │   └── timeline.py
│   │   └── routers/            # 11 routers: auth, checkins, diary, timeline, chat,
│   │                            #   safety, consents, facts, assignments, professional,
│   │                            #   notifications, audit
│   └── requirements.txt
└── frontend/
    └── src/
        ├── api.ts
        ├── auth/AuthContext.tsx
        ├── components/         # CrisisButton, BreathingPacer, NavBar, ProtectedRoute
        └── pages/               # Login/Register, PatientDashboard, DiaryPage, ChatPage,
                                  #  WavePage, SafetyPlanPage, ProfessionalDashboard,
                                  #  AlertsPage, PatientDetailPage
```

## 9. A note on the population this serves

This app is designed around people managing stimulant use and/or chemsex patterns
with elevated suicide risk — a population where the cost of a wrong or clumsy
crisis response is high. That's why the riskiest decisions in this codebase
(alert-level classification, and every word shown at Level 3/4) are deliberately
*not* left to an LLM: they're deterministic, server-owned, and traceable. If you
extend this app, I'd keep that boundary intact even where it's tempting to let
the model be "smarter" about it.
