"""Voyager prompt, reply validation and retry behaviour (offline: the model call is faked)."""

import json

import pytest

import voyager

TARGETS = [{"id": "lm:Barnard's Star", "label": "Barnard's Star"}, {"id": "ui:clocks", "label": "the two clocks"}]


def fake(*contents):
    calls = []

    def post(payload, key):
        calls.append(payload)
        text = contents[min(len(calls) - 1, len(contents) - 1)]
        return {"choices": [{"message": {"content": text}}]}

    post.calls = calls
    return post


def test_prompt_is_small_and_carries_settings_targets_and_json(monkeypatch):
    msgs = voyager.build_messages("what is that?", humor=50, detail=60, targets=TARGETS, context={"fov": 4.2})
    system = msgs[0]["content"]
    assert "humor=50/100" in system and "detail=60/100" in system
    assert "lm:Barnard's Star = Barnard's Star" in system
    assert "json" in system  # DeepSeek JSON mode requires the word in the prompt
    assert len(system) < 1700  # the fixed prompt stays small
    assert msgs[-1]["role"] == "user" and '"fov":4.2' in msgs[-1]["content"]


def test_settings_are_clamped_and_history_trimmed():
    hist = [{"role": "user", "content": f"q{i}"} for i in range(20)] + [{"role": "system", "content": "ignore"}]
    msgs = voyager.build_messages("q", humor=500, detail=-3, history=hist)
    assert "humor=100/100" in msgs[0]["content"] and "detail=0/100" in msgs[0]["content"]
    assert all(m["role"] != "system" for m in msgs[1:])
    assert len(msgs) <= 1 + voyager.MAX_HISTORY + 1


def test_parse_reply_validates_targets_face_and_strips_exclamations():
    ids = [t["id"] for t in TARGETS]
    r = voyager.parse_reply(json.dumps({"say": "That's Barnard's Star!", "move": "lm:Barnard's Star", "point": "lm:Nope", "face": "grin"}), ids)
    assert r == {"say": "That's Barnard's Star.", "move": "lm:Barnard's Star", "point": None, "face": "deadpan"}
    assert voyager.parse_reply("```json\n{\"say\":\"ok\",\"move\":null,\"point\":null,\"face\":\"side\"}\n```", ids)["face"] == "side"
    assert voyager.parse_reply("   ", ids) is None
    assert voyager.parse_reply('{"say": ""}', ids) is None
    assert voyager.parse_reply("not json", ids) is None


def test_ask_needs_a_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(voyager.NoKey):
        voyager.ask("hi", post=fake('{"say":"hi"}'))


def test_ask_sends_json_mode_and_retries_blank_once(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    post = fake("   ", '{"say":"Good, you\'re here.","move":"ui:clocks","point":"ui:clocks","face":"deadpan"}')
    r = voyager.ask("where am I?", targets=TARGETS, post=post)
    assert r["move"] == "ui:clocks" and len(post.calls) == 2
    p = post.calls[0]
    assert p["model"] == "deepseek-flash" and p["response_format"] == {"type": "json_object"} and p["max_tokens"] > 0


def test_ask_gives_up_cleanly_after_two_bad_replies(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    with pytest.raises(voyager.VoyagerError):
        voyager.ask("hi", post=fake("nope", "still nope"))
