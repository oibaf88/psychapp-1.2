# PsychApp

A locally-run self-regulation and risk-monitoring app for people navigating stimulant
use / chemsex patterns and associated suicide risk (Madrid/Spain context), built from
~20 spec/documentation PDFs provided for this project. It pairs a **deterministic,
fully local risk engine** with a **Claude-powered three-agent LLM layer** for
conversation, free-text analysis and a read-only clinical copilot — none of
which can decide a risk level.

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
- A **professional app**: assigned therapists and supervisors can inspect a
  longitudinal timeline, every deterministic risk calculation, and the exact
  input/output lineage of the analyser. Clinical administrators manage assignments
  but do not receive clinical signal or trace visibility.
- A **strict separation between facts and inferences**: things a person or clinician
  has explicitly stated (`ConfirmedFact`) are never silently overwritten by anything
  the system infers from behavior or language (`AlfaSignal`).
- A **deterministic risk engine** (not an LLM) that turns recent signals and facts
  into an `alert_level` 0–4 through an explicit, auditable rule cascade — because
  risk classification in a system like this must be traceable and reproducible, not
  a black box.
- A **three-agent LLM architecture**: one agent talks to the patient (empathetic,
  grounded in MI/CBT/DBT-STOP/urge-surfing techniques, always redirecting to crisis
  resources when needed, and explicitly never allowed to compute or state a risk
  level itself); a second, separate **analyser** only ever reads free text the user
  wrote and returns structured data about it, never talking to anyone; a third is a
  read-only **copilot** for the therapist, which can create no records at all.
- **What is normal is normal *for this person*.** The analyser used to judge every
  patient against the same constants, so `rumination > 0.60` meant the same thing
  for someone who writes in long anxious spirals and someone who answers in four
  words. A per-patient linguistic baseline now sits alongside those constants — a
  reading counts as notable if it is high in absolute terms **or** unusual for that
  person. Never instead of: a patient whose baseline is genuinely alarming must not
  become invisible because it has become normal for them.
- **The clinician can overrule the model.** Both an extracted social observation and
  a linguistic signal can be refuted, which records a `correction` fact and removes
  the inference from the next evaluation. An inference nobody can contradict is one
  nobody should have to act on.
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
- **LLM**: Anthropic Python SDK, calling Claude via the Messages API, or any
  OpenAI-compatible server for a model you host yourself (see below). Each
  role has its own configurable model and effort:
  `ANTHROPIC_CHAT_MODEL` for Agent 1 (conversation),
  `ANTHROPIC_ANALYSIS_MODEL` for the analyst, and
  `ANTHROPIC_COPILOT_MODEL` for Agent 3 (the therapist's copilot — blank
  reuses the Agent 1 values). One endpoint serves all three; the model is
  chosen per call, so there is a single active configuration rather than
  three that can drift apart. The analyst uses **structured outputs**
  (`output_config.format`), so its result is always a JSON object matching a
  fixed schema, never free text.
- **Orchestration**: Docker Compose locally (db + backend + frontend). For a
  hosted deployment — Render for the services, Supabase for Postgres — see
  [DEPLOY.md](./DEPLOY.md).

### Claude by default, your own model if you want one

Claude does not distribute downloadable model weights, so "the Claude model"
cannot run offline: with the default configuration the chat and analysis calls
go to Anthropic's API, and everything else in this app (database, the risk
engine, the UI, all patient/clinical data) stays on your machine.

The LLM call sits behind a small `LLMProvider` interface
(`backend/app/services/llm/`), and there are now two implementations: Claude
over the Anthropic API, and any server speaking the OpenAI chat-completions
API — llama.cpp, Ollama, LM Studio, vLLM, LocalAI. **Ajustes → Modelo de
lenguaje** switches between them at runtime, so you can see how the app
behaves on a model you host yourself without redeploying.

That switch is **off unless you turn it on**: set
`LLM_ALLOW_RUNTIME_OVERRIDE=true`, and sign in as an `admin_clinical`
account. Both gates exist for the same reason — redirecting the agents makes
the server send patient text to whatever URL is named, so it belongs to
whoever runs the deployment, not to whoever happens to be signed in. Everyone
else can still see which model is answering; they just cannot change it. The
hosted blueprint (`render.yaml`) pins it to `false`.

Three things are worth knowing before pointing it somewhere else:

- **Every interaction records the model behind it.** Each assistant turn and
  each analysis stores its provider, model and endpoint, so a patient's
  history stays readable across a change of model: an analysis from March
  under Claude and one from April under a local Llama are both legible, and
  distinguishable. Rows written before this existed say "sin modelo
  registrado" rather than being backfilled with a guess.
- **The risk engine does not change.** Alert levels come from deterministic
  rules over stored data. No model — Claude or otherwise — has ever decided
  one, and none does now.
- **Linguistic detection does change.** The analyser's ability to spot a marker is
  a property of the model reading the text. A weaker model can miss a signal
  that would have raised a level; the signals it does emit go through exactly
  the same cascade. That cost is real, and the Settings screen states it.

### The analyser knows who is writing

The change that most affects what a clinician sees. Originally each agent read
one message with no idea who wrote it, judged against constants identical for
every patient. A person announcing they had decided to change their life for the
better was read as a suicide crisis: with no history, a model cannot tell a
turning point from a euphemism for closure, and the prompt only ever pushed it
to look for what was being hidden.

Five things changed, and none of them lowers the ceiling of any rule:

- **One analyser instead of two agents.** The linguistic and social reads were
  two calls over the same text with disjoint schemas and nothing to reconcile.
  They are one call now: chat went from 3 provider calls per message to 2, the
  diary from 2 to 1.
- **A personal baseline.** `patient_profiles` holds the mean and σ of that
  patient's own scores. The engine asks "high, **or** unusual for them?" — an OR,
  because replacing the constant would let an alarming baseline become invisible.
  A patient with no profile is evaluated exactly as before.
- **A counterweight in the prompt.** Change talk, plans with a future and asking
  for help are named as *not* ideation, with worked examples, and the cost of a
  false positive is stated: a wrong emergency alert teaches a person that saying
  good things here has consequences.
- **A way to disagree.** A therapist can refute a linguistic signal; it stops
  counting from the next evaluation and the reason is stored as a `correction`
  fact. The risk engine needed no change for this — it already filtered inactive
  signals.
- **Direction, not interrogation.** Agent 1 arrives with one or two threads worth
  returning to, offers rather than asks, and drops the agenda entirely at alert
  level 3 or 4.

The therapist panel shows the portrait, the agenda and the baseline, and lets the
assigned therapist correct all three. A model's summary of a patient that nobody
can correct is one nobody should trust.

## 3. How it maps to the spec docs

| Area | What's implemented | Source material |
|---|---|---|
| Fact vs. inference model | `ConfirmedFact` / `AlfaSignal` tables, facts immutable except via versioned correction | "Muro de Hechos vs Inferencias" docs |
| Deterministic risk engine | `app/services/risk_engine.py` — evaluates all 17 rules, persists an immutable `calculation_trace` with formulas, z-scores, thresholds, evidence, matched/selected rules and the final conclusion; the professional UI renders this snapshot without recalculating it | risk-engine pseudocode/schema docs |
| Three-agent LLM architecture | Agent 1 (`AGENT1_SYSTEM_PROMPT`, conversational, never computes risk), the analyser (`ANALYZER_SYSTEM_PROMPT`, strict structured output, never talks to anyone) and Agent 3 (`clinical_copilot.py`, read-only for the therapist). `agent2_analysis_traces` links exact chat/diary source, validated output signal, provider metadata and the risk assessment that consumed it | final two-agent architecture summary docs, extended |
| Accumulated knowledge of the patient | `patient_profiles`: a personal linguistic baseline (mean/σ of that patient's own scores) plus a narrative portrait and a live agenda of open threads, both correctable by the therapist. The engine reads the baseline only to ask "is this unusual for them?" | beyond the docs — see **The analyser knows who is writing** below |
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

### Checking that the Claude agents work

The analyser fails safely by design: if the analysis call breaks, the deterministic
risk engine and server-owned crisis path continue. Every new attempt is recorded
as `started` and finalized with an allow-listed status; therapists and supervisors
can see successful and failed attempts in the analysis-tracking screen. A direct
provider smoke check is also available:

```bash
# from the project root, with the stack running
docker compose exec backend python scripts/smoke_llm.py

# or from backend/, with ANTHROPIC_API_KEY set in the environment
python scripts/smoke_llm.py
```

It calls both agents once, prints Agent 1's reply and the analyser's parsed JSON,
and exits non-zero if either fails. It writes nothing to the database and
seeds no data. On a hosted deployment, run it from the service shell (on
Render: the service's **Shell** tab, `python scripts/smoke_llm.py`).

Runtime failures of the analyser are logged only with a sanitized exception class;
raw provider bodies, clinical prompts, API keys and stack traces are not copied
into trace records or logs.

### Running the backend outside Docker (optional)

If you want to run the API directly against a local Postgres instead of the
`db` container: point `DATABASE_URL` in `.env` at your Postgres instance, then
from `backend/`: `pip install -r requirements.txt && uvicorn app.main:app --reload`.

### Using a dedicated Postgres schema

By default the tables are created in `public`. To put them in their own schema
instead — recommended on Supabase, because `public` is exposed through
PostgREST — either set `DATABASE_SCHEMA`:

```
DATABASE_SCHEMA=psychdeep_v12
```

or carry it in the URL, which is what a hosted deployment usually does:

```
DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:5432/postgres?sslmode=require&options=-csearch_path%3Dpsychdeep_v12
```

Use one or the other, not both. On Supabase the schema, the backend role and
every table come from `supabase/migrations/`, applied with `supabase/deploy.sh`
before the release that needs them — `create_all()` never runs in production.
For a local Postgres, `create_all()` still creates the tables but not the
schema, so create that first:

```sql
create schema if not exists psychdeep_v12;
```

`supabase/verify.sql` checks a Supabase deployment against the contract the API
enforces at startup. `supabase/harden.sql` is the fallback for tables that did
not come from these migrations; it has a `TARGET_SCHEMA` at the top that must
be edited to match, or it will report success against an empty `public`
schema.

## 5. Verification status

The current risk-explanation and analysis-tracking release is machine-verified
before publication:

- the React/TypeScript production build completes;
- the backend imports and compiles under Python 3.11;
- the backend unit/security suite passes in its Linux Docker image;
- SQLAlchemy creates the full model graph in PostgreSQL 17;
- the production migration was run twice against a production-like role/schema
  replica, including a forced-failure rollback test;
- the deterministic engine produced the same level and selected rule as the
  previous implementation across 300 generated input combinations; and
- an interleaving test confirmed two simultaneous analysis signals cannot cross
  their clinical lineage.

These checks do not replace the post-deployment checks in [DEPLOY.md](./DEPLOY.md):
the Supabase migration must be applied before the Render release, then the live
health endpoint, one synthetic analysis call, RBAC, RLS and the rendered clinical
screens must be verified again.

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
modeling in this build, so `supervisor` currently has clinical read visibility
across the patient roster. `admin_clinical` is separately blocked from clinical
facts, signals, risk calculations and analysis traces.

**(e) `structural_score` persistence uses distinct calendar days.** Multiple
evaluations on the same day do not satisfy a multi-day persistence rule. The
snapshot records the observed dates and the required 1/3/5-day threshold.

**(f) Explicit production migrations.** Local/dev still uses
`Base.metadata.create_all()`, but production never mutates the schema at startup.
Supabase changes live under `supabase/migrations/`; the API refuses to start if
the required analysis/risk-explanation columns, owner, FORCE RLS and backend policy
are missing.

**(g) Every LLM agent runs on Claude by default, not separate fine-tuned open
models** — though they can now be pointed at a model you host, from Ajustes. An
earlier doc explored fine-tuning distinct open models per agent. Per the explicit
brief for this build, all three call Claude via the Anthropic API by default,
distinguished only by system prompt and (for the analyser) a forced
structured-output schema. Each role has its own model and effort setting
(`ANTHROPIC_CHAT_MODEL`, `_ANALYSIS_MODEL`, `_COPILOT_MODEL`) and the model is
chosen per call, so one endpoint serves all three rather than three
configurations that can drift apart. The `LLMProvider` abstraction in
`app/services/llm/` is the seam that makes the alternative possible: a second
implementation talks to any OpenAI-compatible server, and Ajustes switches
between them at runtime — for an `admin_clinical` account, on a deployment that
opted in. What has not changed is that a role is a prompt plus a model setting;
this is still not per-agent fine-tuning.

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
│   │   ├── models.py          # SQLAlchemy models (18 tables)
│   │   ├── schemas.py         # Pydantic request/response models
│   │   ├── security.py        # JWT auth + bcrypt hashing
│   │   ├── seed.py            # idempotent demo data
│   │   ├── content/
│   │   │   ├── prompts.py         # Agent 1 / 2 / 3 / 4 prompts + tool schemas
│   │   │   └── safety_resources.py # static Spanish crisis copy + resources
│   │   ├── services/
│   │   │   ├── llm/                # swappable LLM provider (Anthropic + OpenAI-compatible)
│   │   │   ├── llm_config.py       # which model serves the app, and since when
│   │   │   ├── baseline.py         # local structural_score / confidence_band
│   │   │   ├── risk_engine.py      # deterministic alert_level cascade
│   │   │   ├── psychosocial.py     # social-determinant rows + deterministic vulnerability index
│   │   │   ├── profile.py           # per-patient linguistic baseline, portrait, open threads
│   │   │   ├── agent1_context.py    # what Agent 1 is told before it answers
│   │   │   ├── signals.py           # refuting a linguistic inference (writes a `correction` fact)
│   │   │   ├── clinical_view.py    # Spanish explanations, metric series, evidence feed
│   │   │   ├── clinical_copilot.py # Agent 3: therapist <-> LLM, read-only over the record
│   │   │   ├── agent2_trace.py     # privacy-preserving analysis lineage (historic table name)
│   │   │   ├── conversation.py     # Agent2 -> risk_engine -> Agent1 orchestration
│   │   │   ├── notifications.py
│   │   │   ├── audit.py
│   │   │   └── timeline.py
│   │   └── routers/            # 11 routers: auth, checkins, diary, timeline, chat,
│   │                            #   safety, consents, facts, assignments, professional,
│   │                            #   notifications, audit
│   └── requirements.txt
├── docs/MANUAL_TERAPEUTA.md    # full therapist manual (also in-app at /professional/manual)
├── supabase/                   # production schema (migrations/), deploy.sh, verify.sql
└── frontend/
    └── src/
        ├── api.ts
        ├── auth/AuthContext.tsx
        ├── components/         # ClinicalCharts (recharts), ClinicalExplain (narrative +
        │                        #  evidence feed), CopilotPanel, ClinicalTraceability
        │                        #  (raw technical audit UI, now behind a "detail" tab)
        └── pages/               # Login/Register, PatientDashboard, DiaryPage, ChatPage,
                                  #  WavePage, SafetyPlanPage, ProfessionalDashboard,
                                  #  AlertsPage, PatientDetailPage, CopilotPage, ManualPage
```

## 8b. The professional panel

The therapist panel is built around one rule: **a clinician should never have
to read JSON to understand a decision.** Everything the server decided is
returned pre-explained, and the raw trace is available but demoted to a
"Detalle técnico" tab.

| Endpoint | What it returns |
|---|---|
| `GET /professional/patients/{id}/dossier` | Everything below, in one call |
| `GET /professional/patients/{id}/explanation` | Why this patient is at this level, in Spanish, naming the evidence family that drove it |
| `GET /professional/patients/{id}/metrics` | Chart-ready series: level history, structural score, per-variable z-scores, check-ins, linguistic signals, event markers |
| `GET /professional/patients/{id}/evidence` | One row per analysed text: what the patient wrote, what the analyser read in it, which level it produced and which alert it generated |
| `GET /professional/patients/{id}/chat` | The patient's own conversation with Agent 1 |
| `GET /professional/patients/{id}/psychosocial` | Social-determinants index, per-domain breakdown and the literal quotes behind it |
| `POST /professional/patients/{id}/psychosocial/observations/{obs}` | Confirm or refute one extracted observation |
| `POST /professional/patients/{id}/signals/{signal}/refute` | Mark a linguistic inference as wrong. Records a `correction` fact, deactivates the signal and re-evaluates immediately |
| `GET/PUT /professional/patients/{id}/profile` | The accumulated portrait, the open-thread agenda and the personal linguistic baseline — the first two editable by the assigned therapist |
| `GET/POST /professional/patients/{id}/copilot/messages` | Agent 3, the read-only clinical copilot |
| `POST /professional/patients/{id}/copilot/summary` | Fresh situation summary from what the patient has said |

Three design points worth knowing:

- **`structural_score` is similarity, not risk.** It compares the last 7 days
  of check-ins to the patient's own 21-day baseline. `0.91 / stable` next to a
  level-4 alert is correct and expected — the level came from a confirmed fact
  or a chat/diary text, neither of which is in the score. Every explanation
  carries an explicit reconciliation sentence for exactly that case.
- **The composite is a mean of absolute z-scores**, so a large improvement also
  lowers the score. The API therefore also returns an adverse/favourable split
  and a per-variable direction, and the UI leads with those.
- **Chat and diary are both sources.** The analyser reads both, so both are
  readable by the assigned professional and both appear in the evidence feed.

Agent 3 is strictly read-only: it can create no facts, signals, assessments or
alerts, so nothing it says can change a patient's alert level or what the
patient sees. Its system prompt requires a source and a date on every clinical
statement.

Full documentation for clinicians: [`docs/MANUAL_TERAPEUTA.md`](./docs/MANUAL_TERAPEUTA.md),
also served in-app at `/professional/manual`.

## 9. A note on the population this serves

This app is designed around people managing stimulant use and/or chemsex patterns
with elevated suicide risk — a population where the cost of a wrong or clumsy
crisis response is high. That's why the riskiest decisions in this codebase
(alert-level classification, and every word shown at Level 3/4) are deliberately
*not* left to an LLM: they're deterministic, server-owned, and traceable. If you
extend this app, I'd keep that boundary intact even where it's tempting to let
the model be "smarter" about it.
