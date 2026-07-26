"""Shared test fixtures.

Process-wide caches must not leak state between tests: without this,
tests that exercise the real managers become order-dependent (a mapping
cached by one test silently satisfies or breaks a later one).
"""
import pytest


@pytest.fixture(autouse=True)
def _clear_shared_caches():
    from app.db.dynamodb import ModelMappingManager

    ModelMappingManager._cache.clear()
    yield
    ModelMappingManager._cache.clear()
