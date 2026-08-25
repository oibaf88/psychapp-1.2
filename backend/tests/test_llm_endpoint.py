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
        # chat() now carries provenance alongside the text.
        self.assertEqual(reply.text, "hola")
        self.assertEqual(reply.metadata.provider, "openai_compatible")
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
            copilot_model="claude-opus-5",
            anthropic_copilot_model="",
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
            copilot_model="claude-opus-5",
            anthropic_copilot_model="",
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
            copilot_model="claude-opus-5",
            anthropic_copilot_model="",
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
class PerCallModelTests(unittest.TestCase):
    """Three agents, one endpoint, a different model per call.

    Before this, the model was fixed in the provider constructor, so giving
    Agent 3 its own model would have meant a second provider — a second
    client, a second resolution of the active configuration, and two answers
    to "which endpoint is in force".
    """

    def _provider(self, **kwargs):
        return OpenAICompatibleProvider(
            base_url="http://localhost:1234/v1",
            chat_model="chat-model",
            analysis_model="analysis-model",
            **kwargs,
        )

    def _sent(self, client):
        return client.requests[0]["json"]

    def test_chat_uses_the_constructor_model_when_none_is_given(self):
        client = _FakeClient([_response(text="hola")])
        with patch("httpx.Client", return_value=client):
            self._provider().chat("s", [{"role": "user", "content": "h"}])
        self.assertEqual(self._sent(client)["model"], "chat-model")

    def test_chat_honours_a_per_call_model(self):
        client = _FakeClient([_response(text="hola")])
        with patch("httpx.Client", return_value=client):
            result = self._provider().chat(
                "s", [{"role": "user", "content": "h"}], model="copilot-model"
            )
        self.assertEqual(self._sent(client)["model"], "copilot-model")
        # And the provenance follows the model actually asked for.
        self.assertEqual(result.metadata.requested_model, "copilot-model")

    def test_analysis_honours_a_per_call_model(self):
        client = _FakeClient([_response(text='{"a": 1}')])
        with patch("httpx.Client", return_value=client):
            result = self._provider().analyze_structured("s", "texto", SCHEMA, model="other-model")
        self.assertEqual(self._sent(client)["model"], "other-model")
        self.assertEqual(result.metadata.requested_model, "other-model")

    def test_the_copilot_model_defaults_to_the_chat_model(self):
        self.assertEqual(self._provider().copilot_model, "chat-model")

    def test_the_copilot_model_is_used_when_configured(self):
        self.assertEqual(self._provider(copilot_model="big-model").copilot_model, "big-model")

    def test_a_per_call_max_tokens_is_respected(self):
        client = _FakeClient([_response(text="hola")])
        with patch("httpx.Client", return_value=client):
            self._provider(max_tokens=4096).chat("s", [{"role": "user", "content": "h"}], 64)
        self.assertEqual(self._sent(client)["max_tokens"], 64)

    def test_an_omitted_max_tokens_falls_through_to_the_configured_one(self):
        """The old truthy `max_tokens=1024` default shadowed this entirely."""
        client = _FakeClient([_response(text="hola")])
        with patch("httpx.Client", return_value=client):
            self._provider(max_tokens=4096).chat("s", [{"role": "user", "content": "h"}])
        self.assertEqual(self._sent(client)["max_tokens"], 4096)


class RoleSettingsTests(unittest.TestCase):
    """The three roles, and what each falls back to when left unset."""

    def _settings(self, **overrides):
        from app.config import Settings

        return Settings(**overrides)

    def test_an_unset_copilot_model_falls_back_to_chat(self):
        s = self._settings(anthropic_chat_model="chat-x", anthropic_copilot_model="")
        self.assertEqual(s.copilot_model, "chat-x")

    def test_an_unset_copilot_effort_falls_back_to_chat(self):
        s = self._settings(anthropic_chat_effort="medium", anthropic_copilot_effort="")
        self.assertEqual(s.copilot_effort, "medium")

    def test_a_configured_copilot_model_wins(self):
        s = self._settings(anthropic_chat_model="chat-x", anthropic_copilot_model="copilot-y")
        self.assertEqual(s.copilot_model, "copilot-y")

    def test_whitespace_is_not_a_configured_model(self):
        s = self._settings(anthropic_chat_model="chat-x", anthropic_copilot_model="   ")
        self.assertEqual(s.copilot_model, "chat-x")

    def test_the_shared_token_budget_still_serves_both_roles(self):
        """Deployments that set only ANTHROPIC_MAX_TOKENS keep working."""
        s = self._settings(anthropic_max_tokens=8192)
        self.assertEqual(s.max_tokens_chat, 8192)
        self.assertEqual(s.max_tokens_analysis, 8192)

    def test_the_per_role_budgets_override_the_shared_one(self):
        s = self._settings(
            anthropic_max_tokens=8192,
            anthropic_max_tokens_chat=1500,
            anthropic_max_tokens_analysis=16000,
        )
        self.assertEqual(s.max_tokens_chat, 1500)
        self.assertEqual(s.max_tokens_analysis, 16000)

    def test_the_dead_provider_setting_is_gone(self):
        """`llm_provider` was read by nothing; the resolver decides this."""
        from app.config import Settings

        self.assertNotIn("llm_provider", Settings.model_fields)


class CopilotModelPlumbingTests(unittest.TestCase):
    """copilot_model has to survive the whole round trip, or it is decoration."""

    def test_the_environment_config_carries_the_copilot_model(self):
        llm_config.invalidate_cache()
        settings = SimpleNamespace(
            llm_allow_runtime_override=False,
            anthropic_chat_model="chat-x",
            anthropic_analysis_model="analysis-x",
            anthropic_max_tokens=8192,
            copilot_model="copilot-x",
            anthropic_copilot_model="copilot-x",
        )
        with patch.object(llm_config, "get_settings", return_value=settings):
            resolved = llm_config.resolve(db=None)
        self.assertEqual(resolved.copilot_model, "copilot-x")
        self.assertEqual(resolved.public_dict()["copilot_model"], "copilot-x")

    def test_a_stored_row_without_one_reads_as_the_chat_model(self):
        """NULL in an old row means 'same as chat', not 'no model'."""
        row = SimpleNamespace(
            provider="openai_compatible",
            chat_model="llama-chat",
            analysis_model="llama-analysis",
            copilot_model=None,
            base_url="http://localhost:1234/v1",
            api_key=None,
            max_tokens=4096,
            timeout_seconds=120,
            label="local",
            id="row-1",
            created_at=None,
        )
        self.assertEqual(llm_config._from_row(row).copilot_model, "llama-chat")

    def test_validate_treats_a_blank_copilot_model_as_unset(self):
        fields = llm_config.validate(
            provider="anthropic",
            base_url=None,
            chat_model="c",
            analysis_model="a",
            copilot_model="   ",
            max_tokens=4096,
            timeout_seconds=60,
        )
        self.assertEqual(fields["copilot_model"], "")

    def test_the_written_row_records_the_copilot_model(self):
        db = _RecordingSession([])
        llm_config.set_active(
            db,
            provider="openai_compatible",
            base_url="http://localhost:11434/v1",
            chat_model="llama3.1:8b",
            analysis_model="llama3.1:8b",
            copilot_model="llama3.1:70b",
            api_key=None,
            max_tokens=4096,
            timeout_seconds=120,
            label="local",
        )
        self.assertEqual(db.added[0].copilot_model, "llama3.1:70b")

    def test_the_orm_and_the_migration_agree_on_the_column(self):
        import pathlib
        import re

        from app.models import LLMEndpointConfig

        column = LLMEndpointConfig.__table__.columns["copilot_model"]
        self.assertTrue(column.nullable, "NULL is how an old row says 'same as chat'")
        self.assertEqual(column.type.length, 160)

        migrations = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
        sql = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.sql"))
        self.assertTrue(
            re.search(r"add column if not exists copilot_model varchar\(160\)", sql),
            "the ORM column has no matching migration",
        )


class ReviewFindingTests(unittest.TestCase):
    """Four defects a review caught in the per-agent-model change.

    Each is pinned here because each was a case of shipping something that
    reads as working: documented settings that did nothing, a schema check
    that passed while the schema was wrong, a column narrower than what the
    validator accepts, and a form that quietly changed what it was told.
    """

    def test_the_environment_does_not_pin_a_shared_token_budget(self):
        """ANTHROPIC_MAX_TOKENS_CHAT/_ANALYSIS were documented but inert.

        `environment_config()` always fills `max_tokens`, and passing that
        into the provider made it shadow both per-role settings on every
        ordinary call.
        """
        llm_config.invalidate_cache()
        settings = SimpleNamespace(
            llm_allow_runtime_override=False,
            anthropic_chat_model="c",
            anthropic_analysis_model="a",
            anthropic_copilot_model="",
            anthropic_max_tokens=8192,
            copilot_model="c",
        )
        with patch.object(llm_config, "get_settings", return_value=settings):
            self.assertIsNone(llm_config.environment_config().explicit_max_tokens)

    def test_a_runtime_override_does_pin_one(self):
        """A stored row carries one budget for both roles by design."""
        config = llm_config.ResolvedConfig(
            provider="openai_compatible", chat_model="m", analysis_model="m",
            base_url="http://localhost:1234/v1", max_tokens=2048, source="runtime",
        )
        self.assertEqual(config.explicit_max_tokens, 2048)

    def test_the_per_role_budgets_reach_the_anthropic_provider(self):
        """The end-to-end version of the first test."""
        from app.config import Settings
        from app.services.llm import build_provider

        settings = Settings(
            anthropic_api_key="",
            anthropic_max_tokens=8192,
            anthropic_max_tokens_chat=1500,
            anthropic_max_tokens_analysis=16000,
        )
        llm_config.invalidate_cache()
        with patch("app.services.llm.anthropic_provider.get_settings", return_value=settings), \
             patch.object(llm_config, "get_settings", return_value=settings):
            provider = build_provider(llm_config.environment_config())
        self.assertEqual(provider._max_tokens_chat, 1500)
        self.assertEqual(provider._max_tokens_analysis, 16000)
        llm_config.invalidate_cache()

    def test_a_deployment_missing_the_migration_refuses_to_boot(self):
        """Otherwise ORM reads fail and resolve() silently falls back to the
        environment, ignoring a saved override."""
        import inspect
        import pathlib

        from app import main

        self.assertIn('("llm_endpoint_configs", "copilot_model")', inspect.getsource(main))
        verify = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "verify.sql"
        self.assertIn("('llm_endpoint_configs', 'copilot_model')", verify.read_text(encoding="utf-8"))

    def test_provenance_columns_fit_the_model_names_the_config_accepts(self):
        """A 140-character model name must not produce a reply the database
        then refuses to record."""
        from app.models import Agent2AnalysisTrace, LLMEndpointConfig, TherapistCopilotMessage

        accepted = LLMEndpointConfig.__table__.columns["copilot_model"].type.length
        for table, column in (
            (TherapistCopilotMessage, "requested_model"),
            (Agent2AnalysisTrace, "requested_model"),
            (Agent2AnalysisTrace, "response_model"),
        ):
            self.assertGreaterEqual(
                table.__table__.columns[column].type.length,
                accepted,
                f"{table.__tablename__}.{column} is narrower than a valid model name",
            )

    def test_an_inherited_copilot_model_is_reported_as_inherited(self):
        """The UI needs the raw value: prefilling the form with the resolved
        one turns "follows chat" into "pinned to whatever chat was"."""
        inherited = llm_config.ResolvedConfig(
            provider="anthropic", chat_model="chat-x", analysis_model="a",
            copilot_model="chat-x", copilot_model_explicit="",
        )
        pinned = llm_config.ResolvedConfig(
            provider="anthropic", chat_model="chat-x", analysis_model="a",
            copilot_model="copilot-y", copilot_model_explicit="copilot-y",
        )
        self.assertTrue(inherited.copilot_model_is_inherited)
        self.assertFalse(pinned.copilot_model_is_inherited)
        # Resolved for display, raw for the form.
        self.assertEqual(inherited.public_dict()["copilot_model"], "chat-x")
        self.assertEqual(inherited.public_dict()["copilot_model_explicit"], "")

    def test_a_stored_row_that_inherits_reports_it(self):
        row = SimpleNamespace(
            provider="anthropic", chat_model="chat-x", analysis_model="a",
            copilot_model=None, base_url=None, api_key=None, max_tokens=4096,
            timeout_seconds=120, label="", id="r", created_at=None,
        )
        resolved = llm_config._from_row(row)
        self.assertEqual(resolved.copilot_model, "chat-x")
        self.assertTrue(resolved.copilot_model_is_inherited)

    def test_the_settings_form_reads_the_explicit_value(self):
        import pathlib

        page = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "SettingsPage.tsx"
        source = page.read_text(encoding="utf-8")
        self.assertIn("active.copilot_model_explicit", source)
        self.assertNotIn("copilotModel: active.copilot_model ", source)
