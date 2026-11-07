import pytest

from kycscreen.evaluate import build_dataset
from kycscreen.screening import Screener


@pytest.fixture(scope="session")
def small_ds():
    """A tenth-scale dataset: 2,000 customers, 300 watchlist entries, 40 true matches."""
    return build_dataset(2000, scale=0.1)


@pytest.fixture(scope="session")
def screener(small_ds):
    return Screener(small_ds.watchlist, small_ds.freq)
