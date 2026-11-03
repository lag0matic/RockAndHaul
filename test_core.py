"""Offline checks for mineral policies and human-grounded distance limits."""
from .minerals import MINERALS, mineral_name
from .request_limits import request_bounds
from .engine import allowed_url


def test_supported_minerals_and_aliases():
    assert len(MINERALS) == 12
    assert mineral_name("LTD") == "low temperature diamonds"
    assert MINERALS["void opals"]["signal"] == "Void Opal"
    assert MINERALS["osmium"]["signal"] is None


def test_nearby_is_not_an_invented_radius():
    assert all(value is None for value in request_bounds("Find somewhere nearby to mine platinum").values())
    bounds = request_bounds("Find platinum within 100 LY and stations within 2,000 LS")
    assert bounds["max_total_ly"] == 100
    assert bounds["max_approach_ls"] == 2000


def test_network_allowlist():
    assert allowed_url("https://edtools.cc/miner?a=p&cid=46")
    assert not allowed_url("https://example.com/")
