# Dynamic Suggestion Cap

Selora AI limits the number of automation suggestions generated per analysis cycle based on home size. Instead of a fixed cap, the system scales the limit proportionally to the number of **uncovered devices** — devices that are not yet referenced by any existing automation.

---

## How it works

Each collection cycle (`DataCollector._collect_analyze_log`):

1. **Count uncovered devices** — The device registry and entity registry are queried to build a map of device → entity IDs. The full automation configs are read from `automations.yaml` (not from automation state attributes, which only carry metadata like `friendly_name` and `last_triggered`). Entity IDs are extracted recursively from triggers, actions, and conditions. A device is "uncovered" if none of its entities appear in any automation config.

2. **Compute the dynamic cap** — The formula is:

   ```
   cap = clamp(ceil(uncovered_devices / DEVICES_PER_SUGGESTION), MIN, CEILING)
   ```

3. **Propagate to the LLM client** — The cap is set on the LLM client *before* the analysis call, so the prompt asks the LLM to suggest up to N automations.

4. **No early truncation** — `_parse_suggestions` in the LLM client does *not* truncate the response. This is deliberate: if the LLM returns more suggestions than requested, later suggestions may survive deduplication and validation while earlier ones are filtered out. Truncating early would discard potentially novel suggestions.

5. **Post-enrichment cap** — After suggestions are deduplicated against existing automations, validated, and enriched with YAML/risk data, the dynamic cap is applied as the single enforcement point.

```
Device/Entity Registries + automations.yaml
        |
        v
_calculate_dynamic_cap()  ──> cap = clamp(ceil(uncovered / 5), 3, 10)
        |
        v
LLM prompt: "suggest up to {cap}"  ──>  _parse_suggestions: all valid returned
        |
        v
Dedup + validate + enrich  ──>  final truncation to cap
```

## Constants

Defined in `const.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `DEFAULT_MIN_SUGGESTIONS` | 3 | Floor — every home gets at least this many |
| `DEFAULT_MAX_SUGGESTIONS_CEILING` | 10 | Ceiling — never exceed this many |
| `DEFAULT_DEVICES_PER_SUGGESTION` | 5 | Scaling factor — 1 extra suggestion per N uncovered devices |
| `DEFAULT_MAX_SUGGESTIONS` | 3 | Fallback used when `LLMClient` is instantiated without a collector |

## Examples

| Devices | Covered | Uncovered | Formula | Cap |
|---------|---------|-----------|---------|-----|
| 5 | 0 | 5 | ceil(5/5) = 1 → floor | **3** |
| 10 | 2 | 8 | ceil(8/5) = 2 → floor | **3** |
| 20 | 0 | 20 | ceil(20/5) = 4 | **4** |
| 40 | 0 | 40 | ceil(40/5) = 8 | **8** |
| 100 | 0 | 100 | ceil(100/5) = 20 → ceiling | **10** |
| 20 | 18 | 2 | ceil(2/5) = 1 → floor | **3** |

## Coverage detection

A device is considered **covered** when at least one of its entity IDs appears in `automations.yaml`. The full automation configs (triggers, actions, conditions) are read from the YAML file — not from HA automation state attributes, which only carry metadata like `friendly_name`, `last_triggered`, and `id`. Entity IDs are extracted recursively from the config dicts using `DataCollector._extract_entity_ids`.

A device with **no entities** in the entity registry (e.g. a hub or bridge) is always counted as uncovered, which biases the cap upward for homes with many infrastructure devices. This is intentional — infrastructure devices often have child devices that may benefit from suggestions.

## Key files

- `collector.py` — `_calculate_dynamic_cap()`, cap propagation in `_collect_analyze_log()`
- `llm_client.py` — `_parse_suggestions()` enforces `_max_suggestions`
- `const.py` — constant definitions
- `tests/test_suggestion_cap.py` — unit tests
