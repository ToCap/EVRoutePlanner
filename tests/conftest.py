import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clean_cache():
    """Trip results are cached: each test starts from an empty cache."""
    cache.clear()
    yield
    cache.clear()
