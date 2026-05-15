from __future__ import annotations

from pathlib import Path

import pytest

from flyn_memory_router.adapters.hot import HotMemoryMdAdapter
from flyn_memory_router.pin import PinRequest, pin_permanent, unpin


@pytest.fixture
def hot(tmp_path: Path) -> HotMemoryMdAdapter:
    md = tmp_path / "MEMORY.md"
    md.write_text("# MEMORY\n\n## Active pins\n\n")
    return HotMemoryMdAdapter(memory_md=md)


def test_pin_owner_only(hot: HotMemoryMdAdapter):
    req = PinRequest(subject="x", body="b" * 20, sender_role="owner")
    pin_permanent(hot, req)
    assert "x" in hot._md.read_text()


def test_pin_rejects_teammate(hot: HotMemoryMdAdapter):
    req = PinRequest(subject="x", body="b" * 20, sender_role="teammate")
    with pytest.raises(PermissionError):
        pin_permanent(hot, req)


def test_pin_rejects_other(hot: HotMemoryMdAdapter):
    req = PinRequest(subject="x", body="b" * 20, sender_role="other")
    with pytest.raises(PermissionError):
        pin_permanent(hot, req)


def test_unpin_owner_only(hot: HotMemoryMdAdapter):
    hot.pin_permanent("x", "body")
    unpin(hot, "x", sender_role="owner")
    assert "x" not in hot._md.read_text()


def test_unpin_rejects_non_owner(hot: HotMemoryMdAdapter):
    hot.pin_permanent("x", "body")
    with pytest.raises(PermissionError):
        unpin(hot, "x", sender_role="teammate")
