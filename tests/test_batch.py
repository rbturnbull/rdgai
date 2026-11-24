"""
Batch mode: the draft pass sent through Anthropic's Message Batches API (half price, results within hours).

Each request (one unit, or a chunk of a large unit) goes into one batch, with the cached instructions, the unit
prompt and the answer forced as a structured tool call. Answers are checked exactly as in the interactive pass
(``review_answer``): a request whose answer needs another try (missing pairs, an unknown category, a broken declared
constraint) is sent again in the next batch, with the conversation so far and the problem as the tool result. Requests
still failing after the last round are reported as errors. Tests use a fake batch client; nothing is sent anywhere.
"""
import itertools
from types import SimpleNamespace

import pytest

from rdgai.batch import draft_batch, inline_schema, BatchError
from rdgai.pipeline import UnitDecisions, price

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)

UNIT = "Mark.1.1.12-16"


def asked_pairs(params) -> list[tuple[str, str]]:
    prompt = params["messages"][0]["content"]
    section = prompt.split("Pairs to classify")[1]
    pairs = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") and " → " in line:
            a, b = [part.strip() for part in line[2:].split(":")[0].split("→")]
            pairs.append((a, b))
    return pairs


def answer(pairs, category="Major_Omission", alternative=None):
    return {"analysis": "", "decisions": [
        {"active": a, "passive": b, "category": category, "justification": f"{a}>{b}", "confidence": "medium",
         **({"alternative": alternative} if alternative else {})} for a, b in pairs]}


class FakeBatches:
    """ Mimics client.messages.batches: create / retrieve / results, answering each request with `respond(params, round)`. """
    def __init__(self, respond):
        self.respond = respond
        self.batches = []          # the requests of each batch
        self.ids = itertools.count(1)

    def create(self, requests):
        self.batches.append(requests)
        return SimpleNamespace(id=f"batch_{next(self.ids)}", processing_status="in_progress")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        requests = self.batches[int(batch_id.split("_")[1]) - 1]
        for request in requests:
            reply = self.respond(request["params"], len(self.batches))
            if reply is None:
                yield SimpleNamespace(custom_id=request["custom_id"], result=SimpleNamespace(type="errored", error=SimpleNamespace(message="overloaded")))
                continue
            usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=500, cache_creation_input_tokens=0)
            if isinstance(reply, str):          # a reply in prose, without the answer tool
                message = SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)], usage=usage, stop_reason="end_turn")
            else:
                message = SimpleNamespace(content=[SimpleNamespace(type="tool_use", id=f"tu_{request['custom_id']}", name="final_result", input=reply)],
                                          usage=usage, stop_reason="tool_use")
            yield SimpleNamespace(custom_id=request["custom_id"], result=SimpleNamespace(type="succeeded", message=message))


def client(respond):
    return SimpleNamespace(messages=SimpleNamespace(batches=FakeBatches(respond)))


def labels(store, unit_id=UNIT):
    return {(pair.active, pair.passive): pair.category for pair in store.get_unit(unit_id).pairs}


def test_one_batch_with_one_request_per_unit(store):
    fake = client(lambda params, round: answer(asked_pairs(params)))
    report = draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT, "Mark.1.1.10"], client=fake, poll_interval=0)
    batches = fake.messages.batches.batches
    assert len(batches) == 1 and len(batches[0]) == 2
    params = batches[0][0]["params"]
    assert params["model"] == "claude-haiku-5-5"
    assert params["tool_choice"] == {"type": "tool", "name": "final_result"}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Pairs to classify" in params["messages"][0]["content"]
    assert report.pairs_written == len(store.work_pairs(store.get_app(UNIT))) + len(store.work_pairs(store.get_app("Mark.1.1.10")))
    assert report.batch and report.errors == []


def test_a_wrong_answer_is_sent_again_with_the_problem(store):
    def respond(params, round):
        pairs = asked_pairs(params)
        return answer(pairs[:-1]) if round == 1 else answer(pairs)          # first answer misses a pair

    fake = client(respond)
    report = draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=fake, poll_interval=0)
    batches = fake.messages.batches.batches
    assert len(batches) == 2
    follow_up = batches[1][0]["params"]["messages"]
    assert [message["role"] for message in follow_up] == ["user", "assistant", "user"]
    result = follow_up[2]["content"][0]
    assert result["type"] == "tool_result" and result["is_error"] and "Missing pairs" in result["content"]
    assert report.pairs_written == len(store.work_pairs(store.get_app(UNIT)))


def test_a_broken_declared_constraint_is_asked_about_once(store):
    store.set_category_constraints("Major_Omission", ["omits-only"])
    fake = client(lambda params, round: answer(asked_pairs(params)))
    draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=fake, poll_interval=0)
    assert len(fake.messages.batches.batches) == 2                          # asked once; the second answer stands


def test_errored_requests_are_retried_then_reported(store):
    fake = client(lambda params, round: None)
    report = draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=fake, poll_interval=0, max_rounds=2)
    assert len(fake.messages.batches.batches) == 2
    assert report.pairs_written == 0 and report.errors and "overloaded" in report.errors[0]


def test_batch_cost_is_half_the_price(store):
    fake = client(lambda params, round: answer(asked_pairs(params)))
    report = draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=fake, poll_interval=0)
    # as in pydantic-ai, input tokens include the cached ones (Anthropic reports them separately)
    assert report.usage["input_tokens"] == 1500 and report.usage["cache_read_tokens"] == 500 and report.usage["requests"] == 1
    assert report.cost == pytest.approx(price("anthropic:claude-haiku-5-5", report.usage) / 2)


def test_uncertain_pairs_are_collected(store):
    def respond(params, round):
        pairs = asked_pairs(params)
        reply = answer(pairs)
        reply["decisions"][0]["alternative"] = "Minor_Omission"
        return reply

    report = draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=client(respond), poll_interval=0)
    assert len(report.uncertain) == 1 and report.uncertain[0].alternative == "Minor_Omission"


def test_batches_need_an_anthropic_model(store):
    with pytest.raises(BatchError):
        draft_batch(store, "openai:gpt-5", unit_ids=[UNIT], client=client(lambda params, round: None), poll_interval=0)


def test_large_units_are_split_into_requests(store):
    fake = client(lambda params, round: answer(asked_pairs(params)))
    draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=fake, poll_interval=0, max_pairs_per_request=3)
    pairs = len(store.work_pairs(store.get_app(UNIT)))
    assert len(fake.messages.batches.batches[0]) == -(-pairs // 3)


def test_schema_has_no_references():
    schema = inline_schema(UnitDecisions.model_json_schema())
    assert "$defs" not in str(schema) and "$ref" not in str(schema)
    assert schema["properties"]["decisions"]["items"]["properties"]["category"]["type"] == "string"


def test_cli_batch_options():
    from typer.testing import CliRunner
    from rdgai.main import app as cli
    for command in ("draft", "evaluate"):
        assert "--batch" in CliRunner().invoke(cli, [command, "--help"]).output


def test_evaluate_in_batch_mode(tmp_path):
    from .test_cross_validation import path as make_path  # noqa: F401
    import shutil
    from rdgai import sampling
    from rdgai.annotation import AnnotationStore
    from rdgai.evaluate import evaluate
    from .util import TEST_DATA_DIR
    source = tmp_path / "corpus" / "mark_sample.xml"
    source.parent.mkdir()
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", source)
    store = AnnotationStore(source, annotator="RT", check_sample=False)
    units = [sampling.SampleUnit(id="Mark.1.1.10", role="test"), sampling.SampleUnit(id=UNIT, role="example")]
    sampling.save_sample(sampling.Sample(seed=0, size=2, test_fraction=0.5, ignore_types=["defective"], eligible_count=2,
                                         created="2026-11-21T00:00:00Z", source=source.name, units=units), store.sample_path)
    pair = store.get_unit("Mark.1.1.10").pairs[0]
    store.set_classification("Mark.1.1.10", pair.active, pair.passive, "Major_Omission")
    fake = client(lambda params, round: answer(asked_pairs(params)))
    report = evaluate(source, "anthropic:claude-haiku-5-5", output_dir=tmp_path / "out", rounds=0, toolsets=[],
                      batch=True, batch_client=fake, poll_interval=0)
    assert len(fake.messages.batches.batches) == 1
    assert report.draft.accuracy == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# resuming after an interruption, and network errors
# ---------------------------------------------------------------------------

class Interrupted(Exception):
    pass


class FlakyBatches(FakeBatches):
    """ Fails `retrieve` a number of times (a dropped connection), or always after creating (the laptop turned off). """
    def __init__(self, respond, failures=0, crash=False):
        super().__init__(respond)
        self.failures, self.crash, self.created = failures, crash, 0

    def create(self, requests):
        self.created += 1
        return super().create(requests)

    def retrieve(self, batch_id):
        if self.crash:
            raise Interrupted()
        if self.failures:
            self.failures -= 1
            raise ConnectionError("connection reset")
        return super().retrieve(batch_id)


def test_the_batch_state_is_saved_and_a_rerun_resumes_without_paying_again(store, tmp_path):
    state = tmp_path / "state.json"
    crashing = FlakyBatches(lambda params, round: answer(asked_pairs(params)), crash=True)
    with pytest.raises(Interrupted):
        draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=SimpleNamespace(messages=SimpleNamespace(batches=crashing)),
                    poll_interval=0, state_path=state)
    assert state.exists() and crashing.created == 1
    assert labels(store) and all(category is None for category in labels(store).values())    # nothing written yet

    crashing.crash = False                       # the same batches, reachable again after the restart
    report = draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=SimpleNamespace(messages=SimpleNamespace(batches=crashing)),
                         poll_interval=0, state_path=state)
    assert crashing.created == 1                 # no new batch: the first one's results were collected
    assert report.pairs_written == len(store.work_pairs(store.get_app(UNIT)))
    assert not state.exists()                    # finished: the state file is removed


def test_a_state_file_for_another_model_is_refused(store, tmp_path):
    state = tmp_path / "state.json"
    crashing = FlakyBatches(lambda params, round: answer(asked_pairs(params)), crash=True)
    with pytest.raises(Interrupted):
        draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=SimpleNamespace(messages=SimpleNamespace(batches=crashing)),
                    poll_interval=0, state_path=state)
    with pytest.raises(BatchError, match="state"):
        draft_batch(store, "anthropic:claude-opus-5-5", unit_ids=[UNIT], client=client(lambda params, round: None),
                    poll_interval=0, state_path=state)


def test_network_errors_while_waiting_are_retried(store):
    flaky = FlakyBatches(lambda params, round: answer(asked_pairs(params)), failures=2)
    report = draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=[UNIT], client=SimpleNamespace(messages=SimpleNamespace(batches=flaky)),
                         poll_interval=0, retry_wait=0)
    assert report.pairs_written == len(store.work_pairs(store.get_app(UNIT))) and flaky.created == 1


def test_cli_draft_has_a_state_file_option():
    from typer.testing import CliRunner
    from rdgai.main import app as cli
    assert "--state-file" in CliRunner().invoke(cli, ["draft", "--help"]).output


# ---------------------------------------------------------------------------
# models that cannot be forced to use a tool (e.g. Sonnet and Opus 5.5)
# ---------------------------------------------------------------------------

def test_tool_choice_follows_the_model_profile(store):
    fake = client(lambda params, round: answer(asked_pairs(params)))
    draft_batch(store, "anthropic:claude-opus-5-5", unit_ids=[UNIT], client=fake, poll_interval=0)
    params = fake.messages.batches.batches[0][0]["params"]
    assert params["tool_choice"] == {"type": "auto"}
    assert "final_result" in params["messages"][0]["content"]          # the request says how to answer
    fake = client(lambda params, round: answer(asked_pairs(params)))
    draft_batch(store, "anthropic:claude-haiku-5-5", unit_ids=["Mark.1.1.10"], client=fake, poll_interval=0)
    assert fake.messages.batches.batches[0][0]["params"]["tool_choice"] == {"type": "tool", "name": "final_result"}


def test_a_reply_without_the_tool_is_asked_again(store):
    def respond(params, round):
        return "I think these are all omissions." if round == 1 else answer(asked_pairs(params))

    fake = client(respond)
    report = draft_batch(store, "anthropic:claude-opus-5-5", unit_ids=[UNIT], client=fake, poll_interval=0)
    batches = fake.messages.batches.batches
    assert len(batches) == 2
    follow_up = batches[1][0]["params"]["messages"]
    assert [message["role"] for message in follow_up] == ["user", "assistant", "user"]
    assert follow_up[1]["content"][0] == {"type": "text", "text": "I think these are all omissions."}
    assert "final_result" in follow_up[2]["content"]
    assert report.pairs_written == len(store.work_pairs(store.get_app(UNIT)))
