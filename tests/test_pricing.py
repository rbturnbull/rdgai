"""
Pricing a run's usage.

Some models are priced by prompt length per request: Haiku 5.5 bills every token of a request whose prompt exceeds
100,000 tokens at 5x. A run's usage is the sum over many requests, so pricing the sum as one request put every Haiku run
of more than 100,000 tokens in total at the long-context rate (a 5x overstatement). price() spreads the totals over
the requests they came from.
"""
import pytest

from rdgai.pipeline import estimate, price

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)

HAIKU, OPUS = "anthropic:claude-haiku-5-5", "anthropic:claude-opus-5-5"


def per_request_price(model, usage):
    """ The price of `requests` identical requests, each 1/requests of the totals. """
    n = usage["requests"]
    one = {key: value / n for key, value in usage.items() if key != "requests"}
    return n * price(model, dict(one, requests=1))


# fold 1 of a Haiku CV run: 45 requests, each far below 100,000 prompt tokens
HAIKU_FOLD = dict(requests=45, input_tokens=544_703, output_tokens=81_722, cache_read_tokens=341_159, cache_write_tokens=41_491)
# the Opus production run over all of Mark
OPUS_RUN = dict(requests=6_214, input_tokens=65_312_761, output_tokens=8_656_370, cache_read_tokens=45_526_630, cache_write_tokens=0)


def test_a_run_of_short_requests_is_priced_at_the_base_rate():
    # base rates: $0.10 input, $0.01 cache read, $0.125 cache write, $0.50 output per million tokens
    uncached = 544_703 - 341_159 - 41_491
    base = (uncached * 0.10 + 341_159 * 0.01 + 41_491 * 0.125 + 81_722 * 0.50) / 1e6
    assert price(HAIKU, HAIKU_FOLD) == pytest.approx(base, rel=1e-6)
    assert price(HAIKU, HAIKU_FOLD) == pytest.approx(per_request_price(HAIKU, HAIKU_FOLD), rel=1e-9)


def test_one_long_request_is_still_priced_at_the_long_context_rate():
    long = dict(requests=1, input_tokens=150_000, output_tokens=1_000, cache_read_tokens=0, cache_write_tokens=0)
    assert price(HAIKU, long) == pytest.approx((150_000 * 0.50 + 1_000 * 2.50) / 1e6, rel=1e-6)


def test_flat_priced_models_are_unchanged():
    """ Opus has one rate, so spreading makes no difference: the production run's $261.38 (batch: $130.69). """
    assert price(OPUS, OPUS_RUN) == pytest.approx(261.37725, rel=1e-6)


def test_usage_without_a_request_count_is_one_request():
    usage = {key: value for key, value in HAIKU_FOLD.items() if key != "requests"}
    assert price(HAIKU, usage) == pytest.approx(price(HAIKU, dict(usage, requests=1)))


def test_no_requests_costs_nothing():
    assert price(HAIKU, dict(requests=0, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)) == 0.0


def test_the_estimate_prices_each_model_request(store, monkeypatch):
    """ Each unit request is two model turns (a tool call, then the answer); the estimate prices them as such. """
    import rdgai.pipeline as pipeline
    priced = []
    monkeypatch.setattr(pipeline, "price", lambda model, usage: priced.append(usage) or 1.0)
    result = estimate(store, HAIKU)
    assert priced[0]["requests"] == 2 * result.requests
    assert priced[0]["input_tokens"] == result.input_tokens
