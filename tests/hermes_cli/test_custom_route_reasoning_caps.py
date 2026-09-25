"""Declared reasoning ladders from custom OpenAI-compat routes.

``api.kotoba.cloud`` publishes ``reasoningEfforts`` — the ladder its served
configuration actually admits (ADR 2609160940: one llama.cpp instance with a
fixed thinking budget; levels beyond it answer 400 at admission). Hermes
mirrors that declaration from the discovery fetch and clamps the picker and
the wire to it (owner direction 2026-09-16).
"""

import time

import pytest

from hermes_cli import models_reasoning_caps as caps_mod

LADDER = ["none", "minimal", "low", "medium", "high"]


@pytest.fixture(autouse=True)
def _fresh_caches(tmp_path, monkeypatch):
    caps_mod._CUSTOM_CAPS.clear()
    caps_mod._CUSTOM_FAILED_AT.clear()
    monkeypatch.setattr(caps_mod, "_reasoning_caps_disk_path", lambda: tmp_path / "reasoning_caps.json")
    yield
    caps_mod._CUSTOM_CAPS.clear()
    caps_mod._CUSTOM_FAILED_AT.clear()


class TestDeclaredParse:
    def test_declared_ladder_is_authoritative(self):
        parsed = caps_mod.parse_declared_reasoning_efforts(
            {"id": "m", "reasoningEfforts": LADDER})
        assert parsed == {
            "supports_reasoning": True,
            "supported_efforts": LADDER,
            "mandatory": False,
            "authoritative": True,
        }

    def test_offless_ladder_is_mandatory(self):
        parsed = caps_mod.parse_declared_reasoning_efforts(
            {"id": "m", "reasoningEfforts": ["low", " High ", "high"]})
        assert parsed["mandatory"] is True
        assert parsed["supported_efforts"] == ["low", "high"]  # trimmed, lowered, deduped

    def test_no_declaration_is_unknown(self):
        assert caps_mod.parse_declared_reasoning_efforts({"id": "m"}) is None
        assert caps_mod.parse_declared_reasoning_efforts({"id": "m", "reasoningEfforts": []}) is None
        assert caps_mod.parse_declared_reasoning_efforts({"id": "m", "reasoningEfforts": "low"}) is None

    def test_seeder_falls_back_from_openrouter_shape(self):
        items = [
            {"id": "or-model", "supported_parameters": ["tools", "reasoning"],
             "reasoning": {"supported_efforts": ["low", "high"]}},
            {"id": "custom-model", "reasoningEfforts": LADDER},
        ]
        by_id = caps_mod._seed_reasoning_caps("https://route.example/v1/models", items)
        assert by_id["or-model"]["supported_efforts"] == ["low", "high"]
        assert by_id["custom-model"]["authoritative"] is True


class TestSeedAndRead:
    def test_seed_from_discovery_then_cache_only_read(self):
        base = "https://api.kotoba.cloud/v1"
        assert caps_mod.seed_custom_route_reasoning_caps(base, [{"id": "qwen3.8-flash-next-whitehacker", "reasoningEfforts": LADDER}])
        detail = caps_mod.custom_route_model_reasoning_capabilities(
            base, "qwen3.8-flash-next-whitehacker", allow_fetch=False)
        assert detail["supported_efforts"] == LADDER

    def test_url_keying_normalizes_trailing_slash(self):
        caps_mod.seed_custom_route_reasoning_caps("https://api.kotoba.cloud/v1/", [{"id": "m", "reasoningEfforts": LADDER}])
        assert caps_mod.custom_route_model_reasoning_capabilities("https://api.kotoba.cloud/v1", "m", allow_fetch=False) is not None

    def test_route_without_declaration_stays_unknown(self):
        caps_mod.seed_custom_route_reasoning_caps("https://ollama.local/v1", [{"id": "llama3"}])
        assert caps_mod.custom_route_model_reasoning_capabilities("https://ollama.local/v1", "llama3", allow_fetch=False) is None

    def test_cache_only_never_fetches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(caps_mod, "_fetch_reasoning_caps_catalog", lambda *a, **k: calls.append(a) or None)
        assert caps_mod.custom_route_model_reasoning_capabilities("https://cold.example/v1", "m", allow_fetch=False) is None
        assert calls == []


class TestProviderLookup:
    def test_custom_route_base_url_for_provider_matches_provider_key_and_name(self, monkeypatch):
        monkeypatch.setattr(caps_mod, "load_config_readonly", lambda: {}, raising=False)
        entries = [
            {"provider_key": "kotoba", "name": "kotoba", "base_url": "https://api.kotoba.cloud/v1"},
            {"provider_key": "other", "name": "Other", "base_url": "https://other.example/v1"},
        ]
        import hermes_cli.config as cfg_mod
        import hermes_cli.config_providers as cp_mod
        monkeypatch.setattr(cfg_mod, "load_config_readonly", lambda: {})
        monkeypatch.setattr(cp_mod, "get_compatible_custom_providers", lambda config=None: entries)
        assert caps_mod.custom_route_base_url_for_provider("kotoba").endswith("api.kotoba.cloud/v1")
        assert caps_mod.custom_route_base_url_for_provider("KOTOBA") is not None
        assert caps_mod.custom_route_base_url_for_provider("nope") is None
