"""Verify registries auto-wire memory_emitter into adapters with the slot.

Closes the §Δ.adapter-observability threat: "wiring is per-instance, not
central". A registry with a default emitter now propagates it to every
adapter on register/attach, while preserving explicit per-instance
configuration.
"""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from flyn_orchestrator.adapters import (
    ChannelRegistry,
    NotifyRegistry,
    PMRegistry,
)
from flyn_orchestrator.adapters.pm.linear import LinearPMAdapter
from flyn_orchestrator.adapters.pm.olwiki import OLWikiPMAdapter
from flyn_orchestrator.adapters.pm.webhook import WebhookPMAdapter


# ---------------------------------------------------------------------------
# Construction-time wiring (memory_emitter passed to __init__)
# ---------------------------------------------------------------------------

def test_pm_registry_auto_wires_on_register():
    """Adapter registered AFTER registry construction with memory_emitter
    receives the emitter automatically."""
    emitter = MagicMock()
    registry = PMRegistry(memory_emitter=emitter)
    adapter = OLWikiPMAdapter()  # memory_emitter defaults to None
    assert adapter._memory_emitter is None    # pre-register

    registry.register(adapter)
    assert adapter._memory_emitter is emitter    # post-register


def test_channel_registry_auto_wires_on_register():
    """Same auto-wire behavior for ChannelRegistry."""
    from flyn_orchestrator.adapters.channels.email import EmailChannelAdapter
    emitter = MagicMock()
    registry = ChannelRegistry(memory_emitter=emitter)
    adapter = EmailChannelAdapter(config=None)
    assert adapter._memory_emitter is None
    registry.register(adapter)
    assert adapter._memory_emitter is emitter


def test_no_emitter_means_no_wiring():
    """Registry without memory_emitter doesn't touch adapter slots."""
    registry = PMRegistry()  # no emitter
    adapter = OLWikiPMAdapter()
    registry.register(adapter)
    assert adapter._memory_emitter is None  # unchanged


# ---------------------------------------------------------------------------
# Explicit per-instance configuration wins over registry default
# ---------------------------------------------------------------------------

def test_explicit_memory_emitter_is_preserved():
    """An adapter constructed with explicit memory_emitter is NOT overwritten
    by the registry's default."""
    explicit = MagicMock(name="explicit")
    registry_emitter = MagicMock(name="registry")
    registry = PMRegistry(memory_emitter=registry_emitter)

    adapter = OLWikiPMAdapter(memory_emitter=explicit)
    registry.register(adapter)

    # Explicit per-instance config wins
    assert adapter._memory_emitter is explicit
    assert adapter._memory_emitter is not registry_emitter


# ---------------------------------------------------------------------------
# attach_memory_emitter (retro-wiring after adapters are registered)
# ---------------------------------------------------------------------------

def test_attach_memory_emitter_wires_already_registered_adapters():
    """When memory_emitter is constructed AFTER adapters, attach_memory_emitter
    walks the registry and wires each one."""
    registry = PMRegistry()  # no emitter at construction
    a1 = OLWikiPMAdapter()
    a2 = WebhookPMAdapter(target_url="http://stub")
    registry.register(a1)
    registry.register(a2)
    assert a1._memory_emitter is None
    assert a2._memory_emitter is None

    emitter = MagicMock()
    registry.attach_memory_emitter(emitter)
    assert a1._memory_emitter is emitter
    assert a2._memory_emitter is emitter


def test_attach_memory_emitter_preserves_explicit_configuration():
    """Retro-attach must not overwrite adapters with explicit emitters."""
    registry = PMRegistry()
    explicit = MagicMock(name="explicit")
    a1 = OLWikiPMAdapter(memory_emitter=explicit)
    a2 = OLWikiPMAdapter()  # no explicit
    registry.register(a1)
    registry.register(a2)

    retro = MagicMock(name="retro")
    registry.attach_memory_emitter(retro)

    assert a1._memory_emitter is explicit  # preserved
    assert a2._memory_emitter is retro     # wired


def test_attach_memory_emitter_affects_future_registrations():
    """Once attach_memory_emitter is called, subsequent register() calls
    also auto-wire (because the default is now set)."""
    registry = PMRegistry()
    emitter = MagicMock()
    registry.attach_memory_emitter(emitter)

    new_adapter = OLWikiPMAdapter()
    registry.register(new_adapter)
    assert new_adapter._memory_emitter is emitter


# ---------------------------------------------------------------------------
# Adapters without the slot are safely skipped
# ---------------------------------------------------------------------------

def test_adapter_without_memory_emitter_slot_is_skipped():
    """LinearPMAdapter (stub) doesn't have a `_memory_emitter` attribute.
    Registering it through a registry with an emitter must not raise OR
    create the attribute (which would silently hide a real bug)."""
    emitter = MagicMock()
    registry = PMRegistry(memory_emitter=emitter)

    linear = LinearPMAdapter(api_key="stub")
    assert not hasattr(linear, "_memory_emitter")

    registry.register(linear)  # must not raise

    # The attribute should NOT have been auto-created on the LinearPMAdapter
    assert not hasattr(linear, "_memory_emitter")


# ---------------------------------------------------------------------------
# Existing registry behavior preserved
# ---------------------------------------------------------------------------

def test_get_after_register():
    registry = PMRegistry()
    adapter = OLWikiPMAdapter()
    registry.register(adapter)
    assert registry.get("olwiki") is adapter


def test_get_missing_raises_keyerror():
    registry = PMRegistry()
    with pytest.raises(KeyError):
        registry.get("nope")


def test_all_returns_registered_adapters():
    registry = PMRegistry()
    a1 = OLWikiPMAdapter()
    a2 = WebhookPMAdapter(target_url="http://x")
    registry.register(a1)
    registry.register(a2)
    assert set(registry.all()) == {a1, a2}


def test_notify_registry_inherits_same_behavior():
    """NotifyRegistry inherits from _NamedRegistry too — just smoke-test that
    it accepts the kwarg and doesn't break."""
    emitter = MagicMock()
    registry = NotifyRegistry(memory_emitter=emitter)
    # No NotifyAdapter implementations have _memory_emitter slot today; the
    # registry should accept the emitter and be ready for future ones.
    assert registry._default_memory_emitter is emitter
