"""Tier.CONV enum value + ConvMessage stub for downstream tasks."""
from __future__ import annotations


def test_tier_conv_value_exists():
    from flyn_memory_router.types import Tier
    assert Tier.CONV.value == "conv"
    # Existing tiers still present
    assert {t.value for t in Tier} == {"hot", "warm", "cool", "cold", "lesson", "conv"}
