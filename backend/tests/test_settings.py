"""Infrastructure settings read from the environment."""

import pytest

from building_call.settings import Settings


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_a_plain_postgres_url_uses_the_asyncpg_driver(scheme: str) -> None:
    s = Settings(database_url=f"{scheme}://u:p@127.0.0.1:5433/building_call")
    assert s.database_url == "postgresql+asyncpg://u:p@127.0.0.1:5433/building_call"


def test_a_url_with_a_driver_is_kept() -> None:
    url = "postgresql+asyncpg://u:p@127.0.0.1:5433/building_call"
    assert Settings(database_url=url).database_url == url
