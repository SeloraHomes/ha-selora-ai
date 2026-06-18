"""Server-side detection of structural blocks streaming in.

The websocket chat handler stops forwarding tokens to the panel once the
LLM starts emitting an ``automation`` / ``scene`` block — fenced or bare.
Relying on the frontend to hide a fence-less block proved fragile once the
response mixed yaml snippets, prose-in-fence boxes, and the block, so the
JSON leaked into the bubble. ``_STREAM_BARE_BLOCK_RE`` pins the bare-block
detection used by the suppressor.
"""

from __future__ import annotations

from custom_components.selora_ai import (
    _STREAM_BARE_BLOCK_RE,
    _STREAM_FENCED_OPENERS,
    _pending_opener_start,
)


def _replay_stream(chunks: list[str]) -> str:
    """Replay the websocket suppressor over ``chunks`` and return the
    concatenation of everything that would have been forwarded to the
    panel (excluding synthetic spinner sentinels). Mirrors the logic in
    ``__init__.py`` so the test pins end-to-end behaviour, not just the
    regex.
    """
    full_text = ""
    sent_chars = 0
    looks_like_json = False
    sent: list[str] = []
    for chunk in chunks:
        full_text += chunk
        if looks_like_json:
            continue
        opener_idx = -1
        if full_text.lstrip().startswith("{"):
            opener_idx = full_text.index("{")
        else:
            for needle in _STREAM_FENCED_OPENERS:
                idx = full_text.find(needle)
                if idx >= 0 and (opener_idx < 0 or idx < opener_idx):
                    opener_idx = idx
            for bare in _STREAM_BARE_BLOCK_RE.finditer(full_text):
                if full_text.count("```", 0, bare.start()) % 2 == 1:
                    continue
                bare_idx = bare.start()
                if full_text[bare_idx] == "\n":
                    bare_idx += 1
                if opener_idx < 0 or bare_idx < opener_idx:
                    opener_idx = bare_idx
                break
        if opener_idx >= 0:
            looks_like_json = True
            prose = full_text[sent_chars:opener_idx]
            if prose:
                sent.append(prose)
                sent_chars += len(prose)
        else:
            hold = _pending_opener_start(full_text)
            send_to = hold if hold >= 0 else len(full_text)
            prose = full_text[sent_chars:send_to]
            if prose:
                sent.append(prose)
                sent_chars += len(prose)
    return "".join(sent)


def test_matches_bare_automation_json() -> None:
    text = 'I will create a group.\n\nautomation\n{\n  "alias": "X"'
    m = _STREAM_BARE_BLOCK_RE.search(text)
    assert m is not None
    # Anchored at the leading newline before the type word.
    assert text[m.start() :].lstrip().startswith("automation")


def test_matches_bare_scene_json() -> None:
    assert _STREAM_BARE_BLOCK_RE.search('Here.\n\nscene\n{"name":"x"}') is not None


def test_ignores_bare_automation_yaml_body() -> None:
    # YAML-bodied bare blocks are NOT backend-extractable
    # (``parse_streamed_response`` only salvages a ``{`` body), so they
    # must NOT be suppressed — otherwise the final message silently loses
    # the block. They stream through as raw text instead.
    assert _STREAM_BARE_BLOCK_RE.search("ok\nautomation\nalias: X\ntriggers:") is None


def test_ignores_bare_command_block() -> None:
    # Only automation / scene are salvaged bare; a bare ``command\n{…}``
    # has no parser fallback, so it must keep streaming.
    assert _STREAM_BARE_BLOCK_RE.search('ok\ncommand\n{"foo": 1}') is None


def test_ignores_prose_mention() -> None:
    # "automation" on its own line but the next line is prose, not a body.
    assert _STREAM_BARE_BLOCK_RE.search("I built an automation\nthat notifies you.") is None


def test_ignores_inline_word() -> None:
    assert _STREAM_BARE_BLOCK_RE.search("use the group in an automation to notify me") is None


def test_ignores_yaml_config_block() -> None:
    # The model's `yaml\nbinary_sensor: …` config snippet is not a proposal
    # block and must keep streaming to the user.
    assert _STREAM_BARE_BLOCK_RE.search("yaml\nbinary_sensor:\n- platform: group") is None


def test_ignores_automation_yaml_key_heading() -> None:
    # `automation:` (with the colon) is the HA config key, not a bare block.
    assert _STREAM_BARE_BLOCK_RE.search("automation:\n  - alias: x") is None


def test_replay_no_block_token_leaks_char_by_char() -> None:
    # Worst case: one character per chunk. Not a single char of the
    # `automation\n{…}` block may reach the panel — including the partial
    # type word ("Autom…") that used to flash for a couple of seconds.
    full = (
        "I'll create a group, then build the automation.\n\n"
        "automation\n"
        '{\n  "alias": "Water Leak Alert",\n  "triggers": []\n}\n'
    )
    forwarded = _replay_stream(list(full))
    assert "automation\n{" not in forwarded
    assert '"alias"' not in forwarded
    # The leading prose still streams. Note: "the automation." in the
    # prose ends with a period (not a newline + body), so it is never
    # withheld.
    assert forwarded.strip() == "I'll create a group, then build the automation."


def test_replay_realistic_chunks() -> None:
    chunks = [
        "I'll create a group.\n\n",
        "autom",
        "ation\n",
        "{\n",
        '  "alias": "X"\n}\n',
    ]
    forwarded = _replay_stream(chunks)
    assert "autom" not in forwarded.replace("automation.", "")  # no partial word
    assert "alias" not in forwarded
    assert forwarded.strip() == "I'll create a group."


def test_replay_prose_with_inline_automation_word_not_held() -> None:
    # "automation" appears inline (not alone on a line) — must stream.
    full = "Use the group in an automation to notify you when wet.\n"
    assert _replay_stream(list(full)) == full


def test_replay_yaml_config_block_streams_through() -> None:
    # A `yaml` config snippet is not a structural block; it must reach
    # the panel intact.
    full = "Add this:\n\nyaml\nbinary_sensor:\n- platform: group\n"
    assert _replay_stream(list(full)) == full


def test_replay_bare_yaml_automation_streams_through() -> None:
    # Backend can't salvage a YAML-bodied bare automation, so suppressing
    # it would silently lose the block. It must reach the panel as raw
    # text (visible fallback), not be withheld.
    full = "Here:\n\nautomation\nalias: Test\ntriggers:\n  - platform: state\n"
    assert _replay_stream(list(full)) == full


def test_replay_bare_command_streams_through() -> None:
    full = 'Doing it:\n\ncommand\n{"calls": []}\n'
    assert _replay_stream(list(full)) == full


def test_pending_opener_holds_partial_word() -> None:
    # Case-sensitive: the LLM emits a lowercase `automation` type word;
    # an uppercase "Autom" is prose, not a block opener.
    assert _pending_opener_start("ok\nAutom") == -1
    assert _pending_opener_start("ok\nautom") >= 0
    assert _pending_opener_start("ok\nautomation") >= 0
    assert _pending_opener_start("ok\nautomation\n") >= 0
    assert _pending_opener_start("ok\n```autom") >= 0
    # Diverged into a non-opener — released.
    assert _pending_opener_start("ok\nautomatic transmission") == -1
    assert _pending_opener_start("ok\n```yaml") == -1


def test_replay_fenced_example_streams_through() -> None:
    # A bare automation block INSIDE a generic ``` fence is an example,
    # not a proposal — the final parse won't attach a card, so the
    # suppressor must forward it intact instead of stopping the stream
    # and showing a spinner.
    full = (
        "Here's the shape:\n\n"
        "```\n"
        "automation\n"
        '{\n  "alias": "Example"\n}\n'
        "```\n\n"
        "Want one for real?"
    )
    assert _replay_stream(list(full)) == full


def test_replay_real_block_after_fenced_example_suppressed() -> None:
    # The in-fence example streams; a real bare block after it (outside
    # any fence) is still suppressed.
    full = (
        "Example:\n\n```\nautomation\n{}\n```\n\n"
        "Now the real one:\n\n"
        "automation\n"
        '{\n  "alias": "Real"\n}\n'
    )
    forwarded = _replay_stream(list(full))
    assert '"alias": "Real"' not in forwarded
    # Everything up to and including the fenced example is forwarded.
    assert "```\nautomation\n{}\n```" in forwarded
    assert "Now the real one:" in forwarded
