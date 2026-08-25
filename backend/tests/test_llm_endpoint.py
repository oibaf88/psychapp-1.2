"""Tests for the runtime LLM endpoint and the local-model provider.

Two properties matter here. First, that pointing the app at a model you host
yourself actually works against the servers people really run — which in
practice means surviving output that is *nearly* JSON. Second, that being
lenient about punctuation never becomes lenient about content: the strict
Pydantic boundary and the deterministic risk engine are unchanged whichever
model answered.
"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import llm_config
from app.services.llm import build_provider
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.base import StructuredAnalysisError
from app.services.llm.openai_compatible import (
    OpenAICompatibleProvider,
    extract_json_object,
)


class JsonRecoveryTests(unittest.TestCase):
    """A local model's idea of "only JSON" is not the API's."""

    def test_plain_object(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_markdown_fenced(self):
        self.assertEqual(extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_unlabelled_fence(self):
        self.assertEqual(extract_json_object('```\n{"a": 1}\n```'), {"a": 1})

    def test_chatty_preamble_and_epilogue(self):
        text = 'Claro, aquí tienes el JSON:\n{"a": 1, "b": [1, 2]}\nEspero que te sirva.'
        self.assertEqual(extract_json_object(text), {"a": 1, "b": [1, 2]})

    def test_nested_objects_are_not_truncated(self):
        """A brace-counting scan is the point: a regex stops at the first }."""
        text = 'Resultado: {"outer": {"inner": {"deep": true}}, "n": 2} fin'
        self.assertEqual(extract_json_object(text), {"outer": {"inner": {"deep": True}}, "n": 2})

    def test_braces_inside_strings_do_not_confuse_the_scan(self):
        text = '{"quote": "dijo {esto} y {aquello}", "n": 1}'
        self.assertEqual(extract_json_object(text), {"quote": "dijo {esto} y {aquello}", "n": 1})

    def test_escaped_quotes_inside_strings(self):
        text = r'{"quote": "dijo \"hola\" y se fue"}'
        self.assertEqual(extract_json_object(text), {"quote": 'dijo "hola" y se fue'})

    def test_a_bare_array_is_not_an_object(self):
        with self.assertRaises(ValueError):
            extract_json_object("[1, 2, 3]")

    def test_prose_only_raises(self):
        with self.assertRaises(ValueError):
            extract_json_object("No puedo ayudarte con eso.")


def _response(status_code=200, payload=None, text=None):
    body = payload if payload is not None else {
        "id": "cmpl-1",
        "model": "llama-3.1-8b-instruct",
        "choices": [{"message": {"role": "assistant", "content": text or "OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
    }
    return SimpleNamespace(status_code=status_code, json=lambda: body)


class _FakeClient:
    """Stands in for httpx.Client, recording what was actually sent."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def post(self, url, headers=None, json=None):
        # Deep-copied: the provider reuses and mutates one payload dict
        # across retries, so storing the reference would make every
        # recorded request show the last attempt's body.
        self.requests.append(
            {"url": url, "headers": dict(headers or {}), "json": copy.deepcopy(json or {})}
        )
        return self._responses.pop(0)


SCHEMA = {
    "name": "record",
    "input_schema": {
        "type": "object",
        "properties": {"a": {"type": "number"}},
        "required": ["a"],
    },
}


class LocalProviderTests(unittest.TestCase):
    def _provider(self, **kwargs):
        return OpenAICompatibleProvider(
            base_url=kwargs.pop("base_url", "http://localhost:1234/v1"),
            chat_model=kwargs.pop("chat_model", "llama-3.1-8b-instruct"),
            analysis_model=kwargs.pop("analysis_model", "llama-3.1-8b-instruct"),
            **kwargs,
        )

    def test_chat_posts_to_the_standard_path(self):
        client = _FakeClient([_response(text="hola")])
        with patch("httpx.Client", return_value=client):
            reply = self._provider().chat("sistema", [{"role": "user", "content": "hola"}])
        self.assertEqual(reply, "hola")
        self.assertEqual(client.requests[0]["url"], "http://localhost:1234/v1/chat/completions")
        self.assertEqual(client.requests[0]["json"]["messages"][0]["role"], "system")

    def test_no_authorization_header_unless_a_key_is_configured(self):
        """Local runtimes usually have no auth; sending a bogus one breaks some."""
        client = _FakeClient([_response()])
        with patch("httpx.Client", return_value=client):
            self._provider().chat("s", [{"role": "user", "content": "x"}])
        self.assertNotIn("Authorization", client.requests[0]["headers"])

        client = _FakeClient([_response()])
        with patch("httpx.Client", return_value=client):
            self._provider(api_key="secret").chat("s", [{"role": "user", "content": "x"}])
        self.assertEqual(client.requests[0]["headers"]["Authorization"], "Bearer secret")

    def test_structured_analysis_recovers_a_fenced_object(self):
        client = _FakeClient([_response(text='```json\n{"a": 3}\n```')])
        with patch("httpx.Client", return_value=client):
            result = self._provider().analyze_structured("s", "texto", SCHEMA)
        self.assertEqual(result.value, {"a": 3})
        self.assertEqual(result.metadata.provider, "openai_compatible")
        self.assertEqual(result.metadata.base_url, "http://localhost:1234/v1")
        self.assertEqual(result.metadata.response_model, "llama-3.1-8b-instruct")

    def test_extraction_is_deterministic(self):
        """Two readings of one sentence would make a decision unreproducible."""
        client = _FakeClient([_response(text='{"a": 1}')])
        with patch("httpx.Client", return_value=client):
            self._provider().analyze_structured("s", "texto", SCHEMA)
        self.assertEqual(client.requests[0]["json"]["temperature"], 0)

    def test_the_schema_is_restated_in_the_prompt(self):
        """Many servers accept response_format and quietly ignore it."""
        client = _FakeClient([_response(text='{"a": 1}')])
        with patch("httpx.Client", return_value=client):
            self._provider().analyze_structured("sistema", "texto", SCHEMA)
        system_message = client.requests[0]["json"]["messages"][0]["content"]
        self.assertIn("JSON Schema", system_message)
        self.assertIn('"a"', system_message)

    def test_falls_back_when_the_server_rejects_json_schema(self):
        client = _FakeClient(
            [
                _response(status_code=400, payload={"error": "response_format not supported"}),
                _response(text='{"a": 7}'),
            ]
        )
        with patch("httpx.Client", return_value=client):
            result = self._provider().analyze_structured("s", "texto", SCHEMA)
        self.assertEqual(result.value, {"a": 7})
        self.assertEqual(client.requests[0]["json"]["response_format"]["type"], "json_schema")
        self.assertEqual(client.requests[1]["json"]["response_format"]["type"], "json_object")

    def test_falls_back_all_the_way_to_plain_text(self):
        client = _FakeClient(
            [
                _response(status_code=400, payload={}),
                _response(status_code=400, payload={}),
                _response(text='{"a": 9}'),
            ]
        )
        with patch("httpx.Client", return_value=client):
            result = self._provider().analyze_structured("s", "texto", SCHEMA)
        self.assertEqual(result.value, {"a": 9})
        self.assertNotIn("response_format", client.requests[2]["json"])

    def test_a_server_error_does_not_trigger_the_fallback(self):
        """500 means the server broke, not that it dislikes the dialect."""
        client = _FakeClient([_response(status_code=500, payload={})])
        with patch("httpx.Client", return_value=client):
            with self.assertRaises(StructuredAnalysisError) as caught:
                self._provider().analyze_structured("s", "texto", SCHEMA)
        self.assertEqual(caught.exception.safe_kind, "provider_error")
        self.assertEqual(len(client.requests), 1)

    def test_unreachable_endpoint_is_a_named_failure(self):
        import httpx

        with patch("httpx.Client", side_effect=httpx.ConnectError("refused")):
            with self.assertRaises(StructuredAnalysisError) as caught:
                self._provider().analyze_structured("s", "texto", SCHEMA)
        self.assertEqual(caught.exception.error_code, "local_endpoint_unreachable")

    def test_unparseable_output_fails_closed(self):
        client = _FakeClient([_response(text="No puedo ayudarte con eso.")])
        with patch("httpx.Client", return_value=client):
            with self.assertRaises(StructuredAnalysisError) as caught:
                self._provider().analyze_structured("s", "texto", SCHEMA)
        self.assertEqual(caught.exception.safe_kind, "invalid_output")


class BaseUrlNormalisationTests(unittest.TestCase):
    """Accept what people paste from each runtime's own README."""

    def test_bare_host_gets_the_conventional_v1_prefix(self):
        self.assertEqual(llm_config.normalise_base_url("http://localhost:11434"), "http://localhost:11434/v1")

    def test_explicit_v1_is_kept(self):
        self.assertEqual(llm_config.normalise_base_url("http://localhost:1234/v1"), "http://localhost:1234/v1")

    def test_trailing_slash_is_dropped(self):
        self.assertEqual(llm_config.normalise_base_url("http://127.0.0.1:8080/v1/"), "http://127.0.0.1:8080/v1")

    def test_a_pasted_full_endpoint_is_trimmed(self):
        self.assertEqual(
            llm_config.normalise_base_url("http://localhost:1234/v1/chat/completions"),
            "http://localhost:1234/v1",
        )

    def test_a_scheme_is_required(self):
        with self.assertRaises(llm_config.LLMConfigError):
            llm_config.normalise_base_url("localhost:1234")

    def test_empty_is_rejected(self):
        with self.assertRaises(llm_config.LLMConfigError):
            llm_config.normalise_base_url("   ")


class ConfigValidationTests(unittest.TestCase):
    def test_a_local_provider_needs_a_url(self):
        with self.assertRaises(llm_config.LLMConfigError):
            llm_config.validate(
                provider="openai_compatible",
                base_url="",
                chat_model="m",
                analysis_model="m",
                max_tokens=4096,
                timeout_seconds=60,
            )

    def test_anthropic_stores_no_url(self):
        fields = llm_config.validate(
            provider="anthropic",
            base_url="http://localhost:1234",
            chat_model="claude-opus-5",
            analysis_model="claude-opus-5",
            max_tokens=4096,
            timeout_seconds=60,
        )
        self.assertIsNone(fields["base_url"])

    def test_a_model_name_is_required(self):
        with self.assertRaises(llm_config.LLMConfigError):
            llm_config.validate(
                provider="openai_compatible",
                base_url="http://localhost:1234/v1",
                chat_model="  ",
                analysis_model="m",
                max_tokens=4096,
                timeout_seconds=60,
            )


class ProviderSelectionTests(unittest.TestCase):
    def test_a_local_configuration_builds_the_local_provider(self):
        config = llm_config.ResolvedConfig(
            provider="openai_compatible",
            chat_model="llama-3.1-8b-instruct",
            analysis_model="llama-3.1-8b-instruct",
            base_url="http://localhost:1234/v1",
        )
        provider = build_provider(config)
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.base_url, "http://localhost:1234/v1")

    def test_the_default_configuration_builds_the_anthropic_provider(self):
        provider = build_provider(llm_config.environment_config())
        self.assertIsInstance(provider, AnthropicProvider)

    def test_resolution_falls_back_when_the_override_is_disabled(self):
        """A deployment that switches the feature off ignores any stored row."""
        llm_config.invalidate_cache()
        settings = SimpleNamespace(
            llm_allow_runtime_override=False,
            anthropic_chat_model="claude-opus-5",
            anthropic_analysis_model="claude-opus-5",
            anthropic_max_tokens=8192,
        )
        with patch.object(llm_config, "get_settings", return_value=settings):
            resolved = llm_config.resolve(db=None)
        self.assertEqual(resolved.provider, "anthropic")
        self.assertEqual(resolved.source, "environment")

    def test_a_broken_lookup_falls_back_instead_of_taking_the_app_down(self):
        llm_config.invalidate_cache()

        class _ExplodingDb:
            def query(self, _model):
                raise RuntimeError("no database")

        settings = SimpleNamespace(
            llm_allow_runtime_override=True,
            anthropic_chat_model="claude-opus-5",
            anthropic_analysis_model="claude-opus-5",
            anthropic_max_tokens=8192,
        )
        with patch.object(llm_config, "get_settings", return_value=settings):
            resolved = llm_config.resolve(_ExplodingDb())
        self.assertEqual(resolved.provider, "anthropic")
        llm_config.invalidate_cache()

    def test_the_stored_key_is_never_serialised_out(self):
        config = llm_config.ResolvedConfig(
            provider="openai_compatible",
            chat_model="m",
            analysis_model="m",
            base_url="http://localhost:1234/v1",
            api_key="super-secret",
        )
        public = config.public_dict()
        self.assertNotIn("api_key", public)
        self.assertTrue(public["has_api_key"])
        self.assertNotIn("super-secret", str(public))


class ProvenanceTests(unittest.TestCase):
    """History has to stay readable across a change of model."""

    def test_metadata_names_both_the_model_and_the_endpoint(self):
        client = _FakeClient([_response(text='{"a": 1}')])
        with patch("httpx.Client", return_value=client):
            result = OpenAICompatibleProvider(
                base_url="http://192.168.1.40:8080/v1",
                chat_model="mistral-7b",
                analysis_model="mistral-7b",
            ).analyze_structured("s", "t", SCHEMA)
        metadata = result.metadata
        self.assertEqual(metadata.provider, "openai_compatible")
        self.assertEqual(metadata.requested_model, "mistral-7b")
        self.assertEqual(metadata.base_url, "http://192.168.1.40:8080/v1")
        # Two deployments can both request "mistral-7b"; only the endpoint
        # distinguishes which machine actually answered.
        self.assertIsNotNone(metadata.base_url)

    def test_the_server_can_contradict_the_requested_model(self):
        """Notices when the loaded weights are not the configured ones."""
        body = {
            "id": "x",
            "model": "qwen2.5-14b",
            "choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}],
            "usage": {},
        }
        client = _FakeClient([_response(payload=body)])
        with patch("httpx.Client", return_value=client):
            result = OpenAICompatibleProvider(
                base_url="http://localhost:1234/v1",
                chat_model="llama-3.1-8b-instruct",
                analysis_model="llama-3.1-8b-instruct",
            ).analyze_structured("s", "t", SCHEMA)
        self.assertEqual(result.metadata.requested_model, "llama-3.1-8b-instruct")
        self.assertEqual(result.metadata.response_model, "qwen2.5-14b")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """Enough Session to observe the order writes are issued in.

    The invariant under test is a database one — a partial unique index
    allows a single active endpoint — so what matters is that the previous
    row is retired before the replacement is inserted, not what any single
    call returns.
    """

    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.events: list[str] = []
        self.added = []

    def query(self, _model):
        return _FakeQuery([row for row in self.existing if row.is_active])

    def add(self, row):
        self.events.append("insert")
        self.added.append(row)

    def flush(self):
        self.events.append("flush")

    def commit(self):
        self.events.append("commit")

    def refresh(self, _row):
        pass


class _ExistingRow:
    def __init__(self, api_key=None):
        self.id = "old"
        self.is_active = True
        self.deactivated_at = None
        self.api_key = api_key


class ActiveConfigWriteTests(unittest.TestCase):
    """One active endpoint, and a key that survives an unrelated edit."""

    def _set(self, db, **overrides):
        kwargs = dict(
            provider="openai_compatible",
            base_url="http://localhost:11434/v1",
            chat_model="llama3.1:8b",
            analysis_model="llama3.1:8b",
            api_key=None,
            max_tokens=4096,
            timeout_seconds=120,
            label="local",
        )
        kwargs.update(overrides)
        return llm_config.set_active(db, **kwargs)

    def test_the_previous_row_is_retired_before_the_new_one_is_inserted(self):
        previous = _ExistingRow()
        db = _RecordingSession([previous])
        self._set(db)
        self.assertFalse(previous.is_active)
        self.assertIsNotNone(previous.deactivated_at)
        # Without the flush, SQLAlchemy emits the INSERT first and collides
        # with the row this very call is deactivating.
        self.assertLess(db.events.index("flush"), db.events.index("insert"))

    def test_an_omitted_key_keeps_the_stored_one(self):
        db = _RecordingSession([_ExistingRow(api_key="kept-secret")])
        self._set(db, api_key=None)
        self.assertEqual(db.added[0].api_key, "kept-secret")

    def test_an_empty_key_clears_it(self):
        db = _RecordingSession([_ExistingRow(api_key="kept-secret")])
        self._set(db, api_key="")
        self.assertIsNone(db.added[0].api_key)

    def test_an_invalid_configuration_writes_nothing(self):
        previous = _ExistingRow()
        db = _RecordingSession([previous])
        with self.assertRaises(llm_config.LLMConfigError):
            self._set(db, base_url="")
        self.assertEqual(db.events, [])
        self.assertTrue(previous.is_active)


class OverrideGateTests(unittest.TestCase):
    """Who may repoint the agents, and what happens when nobody may.

    Redirecting the endpoint makes the server post patient text to a URL a
    human typed in, so it is gated twice: once at deployment level and once
    at account level. These tests pin both gates and the default.
    """

    def _user(self, role):
        return SimpleNamespace(id="u1", role=role)

    def _settings(self, allow):
        return SimpleNamespace(
            llm_allow_runtime_override=allow,
            anthropic_chat_model="claude-opus-5",
            anthropic_analysis_model="claude-opus-5",
            anthropic_max_tokens=8192,
        )

    def test_the_shipped_default_is_off(self):
        """A deployment that never mentions the variable must not get it on.

        render.yaml did not set it, so the previous `True` default meant the
        hosted install shipped with the override live.
        """
        from app.config import Settings

        self.assertFalse(Settings.model_fields["llm_allow_runtime_override"].default)

    def test_render_blueprint_pins_it_off(self):
        import pathlib

        blueprint = pathlib.Path(__file__).resolve().parents[2] / "render.yaml"
        text = blueprint.read_text(encoding="utf-8")
        self.assertIn("LLM_ALLOW_RUNTIME_OVERRIDE", text)
        # The value is on the line after the key in Render's env-var shape.
        key_line = text.index("LLM_ALLOW_RUNTIME_OVERRIDE")
        self.assertIn('value: "false"', text[key_line : key_line + 120])

    def test_writes_are_admin_only(self):
        """The role check is a dependency, so assert the dependency itself."""
        import inspect

        from app.routers import llm_settings
        from app.security import require_admin

        for handler in (
            llm_settings.update_llm_settings,
            llm_settings.reset_llm_settings,
            llm_settings.test_llm_endpoint,
        ):
            default = inspect.signature(handler).parameters["user"].default
            self.assertIs(
                default.dependency,
                require_admin,
                f"{handler.__name__} must require admin_clinical",
            )

    def test_reading_stays_open_to_any_account(self):
        import inspect

        from app.routers import llm_settings
        from app.security import get_current_user

        default = inspect.signature(llm_settings.read_llm_settings).parameters["user"].default
        self.assertIs(default.dependency, get_current_user)

    def test_require_admin_rejects_a_therapist(self):
        from fastapi import HTTPException

        from app.security import require_roles

        dep = require_roles("admin_clinical")
        with self.assertRaises(HTTPException) as caught:
            dep(user=self._user("therapist"))
        self.assertEqual(caught.exception.status_code, 403)

    def test_status_says_a_therapist_cannot_edit_and_explains_why(self):
        from app.routers import llm_settings

        llm_config.invalidate_cache()
        with patch.object(llm_config, "get_settings", return_value=self._settings(True)), patch.object(
            llm_settings, "get_settings", return_value=self._settings(True)
        ):
            status = llm_settings._status(db=None, user=self._user("therapist"))
        self.assertTrue(status.override_allowed)
        self.assertFalse(status.can_edit)
        self.assertEqual(status.notice, llm_settings.WARNING_NOT_ADMIN)
        llm_config.invalidate_cache()

    def test_status_lets_an_admin_edit_when_the_deployment_allows_it(self):
        from app.routers import llm_settings

        llm_config.invalidate_cache()
        with patch.object(llm_config, "get_settings", return_value=self._settings(True)), patch.object(
            llm_settings, "get_settings", return_value=self._settings(True)
        ):
            status = llm_settings._status(db=None, user=self._user("admin_clinical"))
        self.assertTrue(status.can_edit)
        self.assertIsNone(status.notice)
        llm_config.invalidate_cache()

    def test_an_admin_still_cannot_edit_where_the_deployment_forbids_it(self):
        """The deployment gate is the outer one: role does not override it."""
        from app.routers import llm_settings

        llm_config.invalidate_cache()
        with patch.object(llm_config, "get_settings", return_value=self._settings(False)), patch.object(
            llm_settings, "get_settings", return_value=self._settings(False)
        ):
            status = llm_settings._status(db=None, user=self._user("admin_clinical"))
        self.assertFalse(status.can_edit)
        self.assertEqual(status.notice, llm_settings.WARNING_DISABLED)
        llm_config.invalidate_cache()

    def test_the_write_handlers_refuse_when_the_deployment_forbids_it(self):
        from fastapi import HTTPException

        from app.routers import llm_settings

        with patch.object(llm_settings, "get_settings", return_value=self._settings(False)):
            with self.assertRaises(HTTPException) as caught:
                llm_settings.reset_llm_settings(db=None, user=self._user("admin_clinical"))
            self.assertEqual(caught.exception.status_code, 403)
