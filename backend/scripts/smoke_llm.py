"""Check that both Claude agents actually work, without touching the database.

Agent 2 failures are swallowed at runtime by design (the chat keeps
answering and the risk engine just proceeds without a fresh linguistic
signal), so a broken analyst is invisible from the UI. This script calls
both agents directly and prints what came back.

It makes NO database writes and seeds no data — it only sends two short
strings to the Anthropic API, so it costs two small requests.

Run from the backend/ directory with ANTHROPIC_API_KEY set:

    python scripts/smoke_llm.py

Or inside the running container:

    docker compose exec backend python scripts/smoke_llm.py

Exit code is 0 only if both agents succeed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.content.prompts import (  # noqa: E402
    AGENT1_SYSTEM_PROMPT,
    AGENT2_SYSTEM_PROMPT,
    AGENT2_TOOL_SCHEMA,
)
from app.services.llm import get_llm_provider  # noqa: E402

# Deliberately mild: enough signal for the analyst to return non-zero
# values, nothing that should trip a safety classifier.
SAMPLE_TEXT = (
    "Llevo toda la semana durmiendo fatal y dándole vueltas a lo mismo. "
    "No es nada grave, ya se me pasará, pero estoy cansado de intentarlo."
)

EXPECTED_FIELDS = set(AGENT2_TOOL_SCHEMA["input_schema"]["properties"])


def main() -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("FAIL  ANTHROPIC_API_KEY is not set — nothing to test.")
        return 1

    print(f"chat model     : {settings.anthropic_chat_model} (effort {settings.anthropic_chat_effort})")
    print(f"analysis model : {settings.anthropic_analysis_model} (effort {settings.anthropic_analysis_effort})")
    print()

    provider = get_llm_provider()
    ok = True

    # --- Agent 1: conversational -------------------------------------
    try:
        reply = provider.chat(
            AGENT1_SYSTEM_PROMPT,
            [{"role": "user", "content": SAMPLE_TEXT}],
            max_tokens=300,
        )
        if not reply.strip():
            raise RuntimeError("empty reply")
        print("PASS  Agent 1 (conversational)")
        print(f"      {reply.strip()[:300]}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAIL  Agent 1 (conversational): {type(exc).__name__}: {exc}")

    print()

    # --- Agent 2: structured linguistic analysis ----------------------
    try:
        result = provider.analyze_structured(AGENT2_SYSTEM_PROMPT, SAMPLE_TEXT, AGENT2_TOOL_SCHEMA)
        missing = EXPECTED_FIELDS - set(result)
        if missing:
            raise RuntimeError(f"missing fields in response: {sorted(missing)}")
        print("PASS  Agent 2 (linguistic analysis)")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAIL  Agent 2 (linguistic analysis): {type(exc).__name__}: {exc}")

    print()
    print("RESULT:", "both agents OK" if ok else "at least one agent is broken")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
