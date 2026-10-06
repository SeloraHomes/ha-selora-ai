# Selora AI Local prompts

`local_model/prompts/` holds copies, not sources. The specialists were
fine-tuned on the prompts in the models repo's `data-pipeline/prompts/`, and a
prompt that differs from training puts the model out of distribution with no
error anywhere: the replies just get worse. The Allen benchmark collects
automations but does not score them, so drift in the automation prompt shows up
in no number at all.

- **Never edit the wording here.** A prompt change is made in the models repo
  and arrives with a model release. Each release publishes the prompts in the
  adapter bundle's `prompts/` directory and lists each one in `manifest.json`
  under `system_prompts` as `{filename, size_bytes, sha256}`.
- **`tests/test_selora_local_published_prompts.py` pins every file** to that
  block, copied into `tests/fixtures/selora_local_published_prompts.json`. The
  hash is over the file's raw bytes, as the manifest computes it, so the
  trailing newline counts (the loader strips it before sending, but the pin is
  on the file). On a model release, copy the published files and the release's
  `system_prompts` block together. Never edit one to match the other.
- **`unified_system_prompt.txt` is the fused build's `system` file** (the
  `-ollama` repos). No manifest entry covers it, so the fixture pins it
  separately. No adapter was trained on it, but it is the prompt the fused build
  ships with and is benchmarked against, and the two repos must agree on one.
- **The automation specialist may answer with a blueprint**: a single ```yaml
  block with `blueprint:`, `input:` and `!input`. Nothing here can save a
  blueprint, so `selora_local_blueprint_reply` recognises it and returns it as
  an answer, verbatim, which the panel renders with a Copy button. It runs
  before the deterministic overrides, which build a concrete automation from
  the user's sentence ("…at sunset") without reading the reply, and before any
  JSON is looked for. Blueprint YAML routinely holds `{}`, flow mappings and
  `{{ }}` templates. Without that check, the JSON crop takes one of them for the
  envelope or strips the fence, and an automation turn re-tags the result as an
  automation with nothing in it.
- **Thinking is off in each runtime's own vocabulary.** llama-server reads
  `chat_template_kwargs.enable_thinking`; Ollama ignores that and takes
  `reasoning_effort: "none"` on its OpenAI-compatible endpoint, so the
  ollama-unified backend sends both.
- **The token reservations follow the prompts.** Every reservation is a
  prompt's Qwen3 token count from `_SELORA_LOCAL_PROMPT_TOKENS` plus a fixed
  allowance for everything else the request carries. The ollama-unified
  backend reserves for the unified prompt, because that is what it sends for
  every intent. Sizing for the specialist prompt there overfills the window.
  On a model release, re-measure the counts: encode each stripped file with
  the Qwen3 tokenizer, no special tokens. The published-prompts test ties each
  count to the hash of the file it was measured on.
- **The automation output cap covers the longest trained reply** (a 257-token
  blueprint in v0.5.0), and the automation allowance covers the cap. Raise
  them together.
