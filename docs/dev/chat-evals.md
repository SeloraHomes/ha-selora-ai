# Chat evals

Unit tests and `ChatHarness.chat`/`stream` script the model, so they prove our
code, not what a model does with the prompt. Prompt fixes were judged on a
hub, one failure at a time — and a fix for one case broke the next (an
example meant to stop a tile also taught the model to read a last-camera
sensor instead of the cameras, and the snapshots disappeared).

`tests/evals/` asks a real model questions about a home built to make names
collide (`home.py`) and checks what each turn DID: tools called, tiles shown,
services run, the fact it named. Never the wording.

- **Opt-in, never in CI.** The `eval` marker is deselected by default and the
  cases skip without a key:
  `SELORA_EVAL_PROVIDER=anthropic SELORA_EVAL_API_KEY=… just eval`.
  For Selora Cloud the key is an ACCESS token
  (`SELORA_EVAL_PROVIDER=selora_cloud`, `SELORA_EVAL_CONNECT_URL` for a dev
  Connect), never a refresh token: a refresh can rotate it and unlink the hub
  it came from, so the eval does not refresh and stops when the token expires.
  `SELORA_EVAL_MODEL` picks a model, `SELORA_EVAL_RUNS` repeats each case, and
  `SELORA_EVAL_REPORT=<file>` appends each turn (tools, reply, failures) as
  JSON lines to compare two prompts.
- **A failure from a hub becomes a case before the prompt changes.** Then the
  prompt change ships when every case passes over a few runs (`RUNS=3`): one
  pass of a sampled model proves little.
- **Check behaviour, not text.** `says` takes alternatives and is for the one
  fact that was wrong (which camera, which room); asserting phrasing turns the
  eval into a prompt-wording test.
- **The home is real except Frigate's HTTP.** Entities, areas and the recorder
  are HA's; `fake_frigate()` serves config and events through the real
  `find_camera_events`. `test_home.py` runs in the normal suite so the traps
  cannot quietly stop being traps.
- **The network is opened per test** (`network` fixture): PHCC blocks every
  host but 127.0.0.1 and refuses DNS, and HA's client session resolves on a
  loop the test does not run, so the providers get a session of their own.
