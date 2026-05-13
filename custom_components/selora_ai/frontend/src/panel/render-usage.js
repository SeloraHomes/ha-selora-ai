import { html } from "lit";

// ── Sensor lookup ────────────────────────────────────────────────────
//
// HA derives entity IDs from the entity name when `has_entity_name=True`
// and the device has no explicit name, so we can't assume an entity
// includes "selora" in its slug. Instead we look up by the entity
// registry's platform field (most reliable), then fall back to a pure
// suffix match on hass.states.

const _USAGE_KEYS = [
  "llm_tokens_in",
  "llm_tokens_out",
  "llm_calls",
  "llm_cost",
];

const _USAGE_SENSOR_LABELS = {
  llm_tokens_in: "LLM Tokens In",
  llm_tokens_out: "LLM Tokens Out",
  llm_calls: "LLM Calls",
  llm_cost: "LLM Cost (estimate)",
};

function _findUsageSensors(hass) {
  const result = {};
  if (!hass?.states) return result;

  // Primary: entity registry filtered by platform. This works regardless
  // of how HA slugged the entity_id.
  const entities = hass.entities || {};
  for (const [entityId, entry] of Object.entries(entities)) {
    if (entry?.platform !== "selora_ai") continue;
    if (!entityId.startsWith("sensor.")) continue;
    const uid = entry.unique_id || "";
    for (const key of _USAGE_KEYS) {
      if (uid.endsWith(key)) {
        const state = hass.states[entityId];
        if (state) result[key] = { entityId, state };
      }
    }
  }
  if (Object.keys(result).length === _USAGE_KEYS.length) return result;

  // Fallback: scan states for any sensor whose slug starts with or ends
  // with one of our keys. Handles both prefixed IDs (selora_ai_hub_llm_cost)
  // and suffixed ones (llm_cost_estimate).
  for (const [entityId, state] of Object.entries(hass.states)) {
    if (!entityId.startsWith("sensor.")) continue;
    const slug = entityId.slice(7);
    for (const key of _USAGE_KEYS) {
      if (
        !result[key] &&
        (slug === key || slug.endsWith(key) || slug.startsWith(key))
      ) {
        result[key] = { entityId, state };
      }
    }
  }
  return result;
}

// ── Formatting helpers ───────────────────────────────────────────────

function _fmtTokens(n) {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "k";
  return Math.round(v).toLocaleString();
}

function _fmtUsd(n) {
  const v = Number(n) || 0;
  if (v === 0) return "$0.00";
  if (v < 0.01) return "<$0.01";
  return "$" + v.toFixed(2);
}

function _fmtInt(n) {
  return (Number(n) || 0).toLocaleString();
}

// ── Period stat fetch ────────────────────────────────────────────────
//
// HA's recorder stores long-term statistics for total_increasing sensors.
// `recorder/statistics_during_period` returns per-period buckets with a
// `change` field — exactly what we want to surface ("today's cost",
// "this week's tokens").

async function _fetchPeriodStats(hass, statisticIds, periodStart) {
  if (!hass) return {};
  try {
    const result = await hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: periodStart.toISOString(),
      statistic_ids: statisticIds,
      period: "hour",
      types: ["change"],
    });
    return result || {};
  } catch (err) {
    console.warn("Selora AI: failed to fetch usage statistics", err);
    return {};
  }
}

function _sumChange(buckets) {
  if (!Array.isArray(buckets)) return 0;
  let total = 0;
  for (const b of buckets) {
    const v = Number(b?.change ?? 0);
    if (Number.isFinite(v)) total += v;
  }
  return total;
}

// Called from panel.js when the usage tab is activated. Fetches both
// long-term statistics (period totals) and the recent-events ring buffer
// (per-kind breakdown) in parallel.
export async function loadUsageStats(host) {
  const sensors = _findUsageSensors(host.hass);
  const ids = _USAGE_KEYS.map((k) => sensors[k]?.entityId).filter(Boolean);

  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const startOfWeek = new Date(startOfToday);
  startOfWeek.setDate(startOfWeek.getDate() - 7);
  const startOfMonth = new Date(startOfToday);
  startOfMonth.setDate(1);

  const periodPromise =
    ids.length === 0
      ? Promise.resolve([{}, {}, {}])
      : Promise.all([
          _fetchPeriodStats(host.hass, ids, startOfToday),
          _fetchPeriodStats(host.hass, ids, startOfWeek),
          _fetchPeriodStats(host.hass, ids, startOfMonth),
        ]);

  const recentPromise = host.hass
    .callWS({ type: "selora_ai/usage/recent" })
    .then((r) => (Array.isArray(r?.events) ? r.events : []))
    .catch((err) => {
      console.warn("Selora AI: failed to fetch recent usage events", err);
      return [];
    });

  const pricingPromise = host.hass
    .callWS({ type: "selora_ai/usage/pricing_defaults" })
    .then((r) => r?.pricing || {})
    .catch((err) => {
      console.warn("Selora AI: failed to fetch pricing defaults", err);
      return {};
    });

  const breakdownPromise = host.hass
    .callWS({ type: "selora_ai/usage/breakdown", range: "30d" })
    .then((r) => r?.breakdown || {})
    .catch((err) => {
      console.warn("Selora AI: failed to fetch usage breakdown", err);
      return {};
    });

  const [periods, recent, pricingDefaults, persistedBreakdown] =
    await Promise.all([
      periodPromise,
      recentPromise,
      pricingPromise,
      breakdownPromise,
    ]);

  // Auto-default the filter to the active provider only when the
  // persistent store already has data for that provider. On a fresh
  // upgrade the store starts empty (we don't backfill from the legacy
  // sensors), so the existing sensor-/statistics-based "All providers"
  // view stays the default instead of misleadingly showing $0.
  // Selora Cloud is never recorded locally — clear any prior filter so
  // we don't display stale numbers from the previously active provider.
  if (!host._usageFilterUserSet) {
    const activeProvider = host?._config?.llm_provider || null;
    const activeBucket = activeProvider
      ? persistedBreakdown[activeProvider]
      : null;
    const hasDataForActive =
      activeBucket && Object.keys(activeBucket).length > 0;
    if (
      activeProvider &&
      activeProvider !== "selora_cloud" &&
      hasDataForActive
    ) {
      host._usageFilter = { provider: activeProvider, model: null };
    } else {
      host._usageFilter = { provider: null, model: null };
    }
  }
  const filter = host._usageFilter || { provider: null, model: null };

  // Only fetch filtered totals when a filter is actually active —
  // otherwise the render path falls back to the legacy sensor totals.
  const filteredTotals = filter.provider
    ? await host.hass
        .callWS({
          type: "selora_ai/usage/totals",
          range: "30d",
          provider: filter.provider,
          // Empty string means "the no-model bucket"; null means "any model".
          ...(filter.model != null ? { model: filter.model } : {}),
        })
        .then((r) => r || null)
        .catch((err) => {
          console.warn("Selora AI: failed to fetch filtered totals", err);
          return null;
        })
    : null;
  const [today, week, month] = periods;

  const reduce = (raw) => {
    const out = {};
    for (const key of _USAGE_KEYS) {
      const entityId = sensors[key]?.entityId;
      out[key] = entityId ? _sumChange(raw[entityId]) : 0;
    }
    return out;
  };

  host._usageStats = {
    today: reduce(today),
    week: reduce(week),
    month: reduce(month),
  };
  host._usageRecent = recent;
  host._pricingDefaults = pricingDefaults;
  host._usageBreakdown = persistedBreakdown;
  host._usageFilteredTotals = filteredTotals;
  host.requestUpdate();
}

// ── Breakdown helpers ────────────────────────────────────────────────

const _KIND_LABELS = {
  chat: "Chat",
  chat_tool_round: "Chat — tool calls",
  suggestions: "Suggestion engine",
  command: "One-shot commands",
  session_title: "Session titles",
  health_check: "Health checks",
  raw: "Other",
};

function _kindLabel(kind) {
  return _KIND_LABELS[kind] || kind;
}

const _INTENT_LABELS = {
  command: "command",
  automation: "automation",
  scene: "scene",
  delayed_command: "delayed command",
  cancel: "cancellation",
  clarification: "clarification",
  answer: "answer",
};

function _intentLabel(intent) {
  if (!intent) return "";
  return _INTENT_LABELS[intent] || intent;
}

function _groupByProviderModel(events) {
  const groups = new Map();
  for (const e of events) {
    const key = `${e.provider || "?"}::${e.model || ""}`;
    let g = groups.get(key);
    if (!g) {
      g = {
        kind: `${_providerLabel(e.provider)}${e.model ? ` · ${e.model}` : ""}`,
        calls: 0,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        intents: new Map(),
      };
      groups.set(key, g);
    }
    g.calls += 1;
    g.input_tokens += Number(e.input_tokens) || 0;
    g.output_tokens += Number(e.output_tokens) || 0;
    g.cost_usd += Number(e.cost_usd) || 0;
  }
  return [...groups.values()].sort(
    (a, b) =>
      b.cost_usd - a.cost_usd ||
      b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens),
  );
}

function _groupByKind(events) {
  const groups = new Map();
  for (const e of events) {
    const key = e.kind || "raw";
    let g = groups.get(key);
    if (!g) {
      g = {
        kind: key,
        calls: 0,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        intents: new Map(),
      };
      groups.set(key, g);
    }
    g.calls += 1;
    g.input_tokens += Number(e.input_tokens) || 0;
    g.output_tokens += Number(e.output_tokens) || 0;
    g.cost_usd += Number(e.cost_usd) || 0;
    if (e.intent) {
      g.intents.set(e.intent, (g.intents.get(e.intent) || 0) + 1);
    }
  }
  // Sort by cost desc, then by tokens desc (so the expensive things float).
  return [...groups.values()].sort(
    (a, b) =>
      b.cost_usd - a.cost_usd ||
      b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens),
  );
}

function _formatRelativeTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const now = Date.now();
  const sec = Math.max(1, Math.round((now - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

// ── Dashboard snippet picker ─────────────────────────────────────────

const _SNIPPET_LABELS = {
  llm_cost: "Cost",
  llm_tokens_in: "Tokens in",
  llm_tokens_out: "Tokens out",
  llm_calls: "Calls",
};

function _yamlForSensor(entityId, label) {
  return `type: statistics-graph
title: ${label} per day
entities:
  - ${entityId}
stat_types:
  - change
period: day
days_to_show: 30`;
}

function _highlightYaml(yamlStr) {
  return yamlStr.split("\n").map((line) => {
    const indent = line.match(/^(\s*)/)[1];
    const rest = line.slice(indent.length);
    const listMatch = rest.match(/^(- )(.*)$/);
    if (listMatch) {
      // prettier-ignore
      return html`<div class="yaml-line">${indent}<span class="yaml-dash">- </span><span class="yaml-val">${listMatch[2]}</span></div>`;
    }
    const kvMatch = rest.match(/^([\w_-]+)(:)(.*)$/);
    if (kvMatch) {
      const val = kvMatch[3].trim();
      // prettier-ignore
      return html`<div class="yaml-line">${indent}<span class="yaml-key">${kvMatch[1]}</span><span class="yaml-colon">:</span>${val ? html` <span class="yaml-val">${val}</span>` : ""}</div>`;
    }
    // prettier-ignore
    return html`<div class="yaml-line">${line}</div>`;
  });
}

function _renderDashboardSnippet(host, sensors) {
  const selected = host._dashboardSnippetKey || _USAGE_KEYS[0];
  const s = sensors[selected];
  const entityId = s?.entityId || `sensor.${selected}`;
  const label =
    s?.state?.attributes?.friendly_name || _USAGE_SENSOR_LABELS[selected];
  const yaml = _yamlForSensor(entityId, label);

  return html`
    <div class="usage-snippet-pills">
      ${_USAGE_KEYS.map(
        (key) => html`
          <button
            class="usage-snippet-pill ${key === selected ? "active" : ""}"
            @click=${() => {
              host._dashboardSnippetKey = key;
              host.requestUpdate();
            }}
          >
            ${_SNIPPET_LABELS[key]}
          </button>
        `,
      )}
    </div>
    <div class="usage-yaml-block" style="position: relative;">
      <code>${_highlightYaml(yaml)}</code>
      <button
        class="usage-copy-btn"
        @click=${(e) => {
          const block = e.currentTarget.closest(".usage-yaml-block");
          const codeEl = block?.querySelector("code");
          if (codeEl) {
            const range = document.createRange();
            range.selectNodeContents(codeEl);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
          }
          const ta = document.createElement("textarea");
          ta.value = yaml;
          ta.style.cssText =
            "position:fixed;left:-9999px;top:-9999px;opacity:0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          const btn = e.currentTarget;
          btn.textContent = "Copied!";
          setTimeout(() => {
            btn.textContent = "Copy";
          }, 1500);
        }}
      >
        Copy
      </button>
    </div>
    <p class="usage-help" style="margin-top: 8px;">
      The visual card picker will also find these sensors after the Recorder's
      first hourly compilation.
    </p>
  `;
}

// ── Sub-views ────────────────────────────────────────────────────────

function _renderTile({ label, value, sub, icon }) {
  return html`
    <div class="usage-tile">
      <div class="usage-tile-head">
        ${icon
          ? html`<ha-icon icon=${icon} style="--mdc-icon-size:16px;"></ha-icon>`
          : ""}
        <span class="usage-tile-label">${label}</span>
      </div>
      <div class="usage-tile-value">${value}</div>
      ${sub ? html`<div class="usage-tile-sub">${sub}</div>` : ""}
    </div>
  `;
}

function _renderPeriodRow(title, stats) {
  if (!stats) {
    return html`
      <div class="usage-period-row usage-period-row--loading">
        <span class="usage-period-title">${title}</span>
        <span class="usage-period-loading">Loading…</span>
      </div>
    `;
  }
  const tokensIn = stats.llm_tokens_in || 0;
  const tokensOut = stats.llm_tokens_out || 0;
  const calls = stats.llm_calls || 0;
  const cost = stats.llm_cost || 0;
  const empty = !tokensIn && !tokensOut && !calls && !cost;
  return html`
    <div class="usage-period-row">
      <span class="usage-period-title">${title}</span>
      ${empty
        ? html`<span class="usage-period-empty">No activity</span>`
        : html`
            <span class="usage-period-cost">${_fmtUsd(cost)}</span>
            <span class="usage-period-tokens">
              ${_fmtTokens(tokensIn + tokensOut)} tokens · ${_fmtInt(calls)}
              calls
            </span>
          `}
    </div>
  `;
}

function _renderBreakdown(groups, totalCost) {
  if (!groups || groups.length === 0) return "";
  return html`
    <div class="usage-breakdown">
      ${groups.map((g) => {
        const pct =
          totalCost > 0 ? Math.round((g.cost_usd / totalCost) * 100) : 0;
        const tokens = g.input_tokens + g.output_tokens;
        const intentEntries = [...g.intents.entries()].sort(
          (a, b) => b[1] - a[1],
        );
        return html`
          <div class="usage-breakdown-row">
            <div class="usage-breakdown-head">
              <span class="usage-breakdown-label">${_kindLabel(g.kind)}</span>
              <span class="usage-breakdown-cost">${_fmtUsd(g.cost_usd)}</span>
            </div>
            <div class="usage-breakdown-bar">
              <div
                class="usage-breakdown-bar-fill"
                style="width:${Math.max(2, pct)}%;"
              ></div>
            </div>
            <div class="usage-breakdown-meta">
              <span>${_fmtInt(g.calls)} call${g.calls === 1 ? "" : "s"}</span>
              <span>·</span>
              <span>${_fmtTokens(tokens)} tokens</span>
              ${totalCost > 0
                ? html`<span>·</span> <span>${pct}% of cost</span>`
                : ""}
            </div>
            ${intentEntries.length > 0
              ? html`
                  <div class="usage-breakdown-intents">
                    ${intentEntries.map(
                      ([intent, count]) => html`
                        <span class="usage-intent-pill">
                          ${_intentLabel(intent)} · ${_fmtInt(count)}
                        </span>
                      `,
                    )}
                  </div>
                `
              : ""}
          </div>
        `;
      })}
    </div>
  `;
}

function _renderRecentList(events) {
  return html`
    <div class="usage-recent-list">
      ${events.map((e) => {
        const intent = _intentLabel(e.intent);
        return html`
          <div class="usage-recent-row">
            <div class="usage-recent-main">
              <span class="usage-recent-kind">${_kindLabel(e.kind)}</span>
              ${intent
                ? html`<span class="usage-recent-intent">→ ${intent}</span>`
                : ""}
              <span class="usage-recent-time">
                ${_formatRelativeTime(e.timestamp)}
              </span>
            </div>
            <div class="usage-recent-details">
              <span class="usage-recent-model">${e.provider} · ${e.model}</span>
              <span class="usage-recent-tokens">
                ${_fmtTokens((e.input_tokens || 0) + (e.output_tokens || 0))}
                tok
              </span>
              <span class="usage-recent-cost">${_fmtUsd(e.cost_usd)}</span>
            </div>
          </div>
        `;
      })}
    </div>
  `;
}

// ── Pricing override helpers ─────────────────────────────────────────

function _activeProviderModel(host) {
  const cfg = host?._config || {};
  const provider = cfg.llm_provider || "anthropic";
  const modelKey =
    provider === "anthropic"
      ? "anthropic_model"
      : provider === "gemini"
        ? "gemini_model"
        : provider === "openai"
          ? "openai_model"
          : provider === "openrouter"
            ? "openrouter_model"
            : provider === "ollama"
              ? "ollama_model"
              : null;
  // selora_local and selora_cloud have no user-visible model id.
  const model = modelKey ? cfg[modelKey] || "" : "";
  return { provider, model };
}

const _PROVIDER_LABELS = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google Gemini",
  openrouter: "OpenRouter",
  ollama: "Ollama",
  selora_local: "Selora AI Local",
  selora_cloud: "Selora Cloud",
};

function _providerLabel(p) {
  return _PROVIDER_LABELS[p] || p;
}

function _defaultPriceFor(host, provider, model) {
  const table = host?._pricingDefaults || {};
  return table[provider]?.[model] || null;
}

function _overridePriceFor(host, provider, model) {
  const overrides = host?._config?.llm_pricing_overrides || {};
  return overrides[provider]?.[model] || null;
}

function _formatPrice(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return "$" + v.toFixed(v < 1 ? 3 : 2).replace(/\.?0+$/, "") + " / MTok";
}

async function _savePricingOverride(host, provider, model, inPrice, outPrice) {
  if (!host?._config) return;
  const current = { ...(host._config.llm_pricing_overrides || {}) };
  const perProvider = { ...(current[provider] || {}) };
  const inNum = Number(inPrice);
  const outNum = Number(outPrice);
  if (
    !Number.isFinite(inNum) ||
    inNum < 0 ||
    !Number.isFinite(outNum) ||
    outNum < 0
  ) {
    host._showToast?.("Pricing must be non-negative numbers.", "error");
    return;
  }
  perProvider[model] = [inNum, outNum];
  current[provider] = perProvider;
  try {
    await host.hass.callWS({
      type: "selora_ai/update_config",
      config: { llm_pricing_overrides: current },
    });
    host._config = { ...host._config, llm_pricing_overrides: current };
    host._pricingEdit = null;
    host._showToast?.("Pricing override saved.", "success");
    host.requestUpdate();
  } catch (err) {
    host._showToast?.("Failed to save pricing: " + err.message, "error");
  }
}

async function _clearPricingOverride(host, provider, model) {
  if (!host?._config) return;
  const current = { ...(host._config.llm_pricing_overrides || {}) };
  const perProvider = { ...(current[provider] || {}) };
  if (!(model in perProvider)) return;
  delete perProvider[model];
  if (Object.keys(perProvider).length === 0) {
    delete current[provider];
  } else {
    current[provider] = perProvider;
  }
  try {
    await host.hass.callWS({
      type: "selora_ai/update_config",
      config: { llm_pricing_overrides: current },
    });
    host._config = { ...host._config, llm_pricing_overrides: current };
    host._pricingEdit = null;
    host._showToast?.("Reset to default pricing.", "success");
    host.requestUpdate();
  } catch (err) {
    host._showToast?.("Failed to reset pricing: " + err.message, "error");
  }
}

const SELORA_CLOUD_USAGE_URL = "https://connect.selorahomes.com/selora-ai";

function _renderPricingCard(host) {
  const { provider, model } = _activeProviderModel(host);
  if (provider === "selora_cloud") {
    return html`
      <div class="section-card">
        <div class="section-card-header">
          <h3>Pricing</h3>
          <span class="usage-section-sub">Selora Cloud</span>
        </div>
        <p class="usage-help" style="margin-top:0;">
          Selora Cloud usage is metered and billed in your Selora Homes account.
          It is not counted in this integration's sensors or charts.
        </p>
        <div class="usage-pricing-actions">
          <a
            class="btn btn-outline"
            href=${SELORA_CLOUD_USAGE_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ha-icon
              icon="mdi:open-in-new"
              style="--mdc-icon-size:16px;"
            ></ha-icon>
            View usage in your Selora Homes account
          </a>
        </div>
      </div>
    `;
  }
  if (provider === "ollama" || provider === "selora_local" || !model) {
    return html`
      <div class="section-card">
        <div class="section-card-header">
          <h3>Pricing</h3>
        </div>
        <p class="usage-help">
          ${provider === "ollama"
            ? "Ollama runs locally — no token costs to track."
            : provider === "selora_local"
              ? "Selora AI Local runs on your hardware — no token costs to track."
              : "Configure an LLM provider and model in Settings to set custom pricing."}
        </p>
      </div>
    `;
  }

  const defaults = _defaultPriceFor(host, provider, model);
  const override = _overridePriceFor(host, provider, model);
  const editing =
    host._pricingEdit?.provider === provider &&
    host._pricingEdit?.model === model;
  const effective = override || defaults;

  return html`
    <div class="section-card">
      <div class="section-card-header">
        <h3>Pricing</h3>
        <span class="usage-section-sub">${provider} · ${model}</span>
      </div>
      <p class="usage-help" style="margin-top:0;">
        Cost estimates use these per-million-token rates. Anthropic defaults
        come from the
        <a
          href="https://platform.claude.com/docs/en/about-claude/pricing"
          target="_blank"
          rel="noopener noreferrer"
          >official pricing page</a
        >; override here if you have negotiated rates or are tracking a
        different model.
      </p>

      <div class="usage-pricing-row">
        <div class="usage-pricing-cell">
          <span class="usage-pricing-label">Input</span>
          <span class="usage-pricing-value">
            ${effective ? _formatPrice(effective[0]) : "—"}
          </span>
          ${defaults
            ? html`<span class="usage-pricing-default">
                default ${_formatPrice(defaults[0])}
              </span>`
            : html`<span class="usage-pricing-default"
                >no built-in default</span
              >`}
        </div>
        <div class="usage-pricing-cell">
          <span class="usage-pricing-label">Output</span>
          <span class="usage-pricing-value">
            ${effective ? _formatPrice(effective[1]) : "—"}
          </span>
          ${defaults
            ? html`<span class="usage-pricing-default">
                default ${_formatPrice(defaults[1])}
              </span>`
            : ""}
        </div>
      </div>

      ${editing
        ? html`
            <div class="usage-pricing-edit">
              <ha-textfield
                label="Input ($/MTok)"
                type="number"
                step="0.01"
                min="0"
                .value=${String(host._pricingEdit.input ?? "")}
                @input=${(e) => {
                  host._pricingEdit = {
                    ...host._pricingEdit,
                    input: e.target.value,
                  };
                }}
                style="flex:1;min-width:120px;"
              ></ha-textfield>
              <ha-textfield
                label="Output ($/MTok)"
                type="number"
                step="0.01"
                min="0"
                .value=${String(host._pricingEdit.output ?? "")}
                @input=${(e) => {
                  host._pricingEdit = {
                    ...host._pricingEdit,
                    output: e.target.value,
                  };
                }}
                style="flex:1;min-width:120px;"
              ></ha-textfield>
              <div class="usage-pricing-actions">
                <button
                  class="btn btn-outline"
                  @click=${() => {
                    host._pricingEdit = null;
                    host.requestUpdate();
                  }}
                >
                  Cancel
                </button>
                <button
                  class="btn btn-primary"
                  @click=${() =>
                    _savePricingOverride(
                      host,
                      provider,
                      model,
                      host._pricingEdit.input,
                      host._pricingEdit.output,
                    )}
                >
                  Save
                </button>
              </div>
            </div>
          `
        : html`
            <div class="usage-pricing-actions">
              <button
                class="btn btn-outline"
                @click=${() => {
                  host._pricingEdit = {
                    provider,
                    model,
                    input: effective ? effective[0] : "",
                    output: effective ? effective[1] : "",
                  };
                  host.requestUpdate();
                }}
              >
                <ha-icon
                  icon=${override ? "mdi:pencil" : "mdi:cash-edit"}
                  style="--mdc-icon-size:16px;"
                ></ha-icon>
                ${override ? "Edit override" : "Set custom pricing"}
              </button>
              ${override
                ? html`
                    <button
                      class="btn btn-outline"
                      @click=${() =>
                        _clearPricingOverride(host, provider, model)}
                    >
                      Reset to default
                    </button>
                  `
                : ""}
            </div>
          `}
    </div>
  `;
}

// ── Main render ──────────────────────────────────────────────────────

export function renderUsage(host) {
  const sensors = _findUsageSensors(host.hass);
  const tokensIn = Number(sensors.llm_tokens_in?.state?.state) || 0;
  const tokensOut = Number(sensors.llm_tokens_out?.state?.state) || 0;
  const calls = Number(sensors.llm_calls?.state?.state) || 0;
  const cost = Number(sensors.llm_cost?.state?.state) || 0;

  const sensorsMissing = Object.keys(sensors).length === 0;
  const stats = host._usageStats || null;
  const recent = Array.isArray(host._usageRecent) ? host._usageRecent : null;
  const persistedBreakdown = host._usageBreakdown || {};
  const filteredTotals = host._usageFilteredTotals || null;
  const filter = host._usageFilter || { provider: null, model: null };
  const groupingMode = host._usageGrouping || "kind";

  const activeProvider = host?._config?.llm_provider || null;
  // Always include the active provider so the user can toggle even if
  // the store hasn't recorded anything for it yet (e.g. just deployed).
  const providerOptionSet = new Set(Object.keys(persistedBreakdown));
  if (activeProvider && activeProvider !== "selora_cloud") {
    providerOptionSet.add(activeProvider);
  }
  const providerOptions = [...providerOptionSet];
  const modelOptions = filter.provider
    ? Object.keys(persistedBreakdown[filter.provider] || {})
    : [];
  const filterActive = Boolean(filter.provider);

  const lastRecentEvent =
    recent && recent.length > 0 ? recent[recent.length - 1] : null;
  const lastProvider =
    sensors.llm_calls?.state?.attributes?.last_provider ||
    sensors.llm_cost?.state?.attributes?.last_provider ||
    lastRecentEvent?.provider ||
    null;
  const lastModel =
    sensors.llm_calls?.state?.attributes?.last_model ||
    sensors.llm_cost?.state?.attributes?.last_model ||
    lastRecentEvent?.model ||
    null;
  const filteredRecent = recent
    ? recent.filter(
        (e) =>
          (!filter.provider || e.provider === filter.provider) &&
          (filter.model == null || (e.model || "") === filter.model),
      )
    : null;
  const breakdown = filteredRecent
    ? groupingMode === "provider"
      ? _groupByProviderModel(filteredRecent)
      : _groupByKind(filteredRecent)
    : null;
  const totalCost = breakdown
    ? breakdown.reduce((sum, g) => sum + g.cost_usd, 0)
    : 0;

  // When sensors aren't registered yet, derive totals from the ring buffer.
  const bufTokensIn = breakdown
    ? breakdown.reduce((s, g) => s + g.input_tokens, 0)
    : 0;
  const bufTokensOut = breakdown
    ? breakdown.reduce((s, g) => s + g.output_tokens, 0)
    : 0;
  const bufCalls = breakdown ? breakdown.reduce((s, g) => s + g.calls, 0) : 0;

  // When a provider/model filter is active, totals + period rows come from
  // the persistent store (filtered). Otherwise we keep the existing
  // sensor-/statistics-based path so existing dashboards stay consistent.
  let dispTokensIn;
  let dispTokensOut;
  let dispCalls;
  let dispCost;
  let periodStats = stats;
  if (filterActive && filteredTotals?.totals) {
    const t = filteredTotals.totals;
    dispTokensIn = t.input || 0;
    dispTokensOut = t.output || 0;
    dispCalls = t.calls || 0;
    dispCost = t.cost_usd || 0;
    const p = filteredTotals.periods || {};
    const pick = (k) => {
      const v = p[k] || {};
      return {
        llm_tokens_in: v.input || 0,
        llm_tokens_out: v.output || 0,
        llm_calls: v.calls || 0,
        llm_cost: v.cost_usd || 0,
      };
    };
    periodStats = {
      today: pick("today"),
      week: pick("7d"),
      month: pick("month"),
    };
  } else {
    dispTokensIn = sensorsMissing ? bufTokensIn : tokensIn;
    dispTokensOut = sensorsMissing ? bufTokensOut : tokensOut;
    dispCalls = sensorsMissing ? bufCalls : calls;
    dispCost = sensorsMissing ? totalCost : cost;
  }
  const hasTotals = dispTokensIn || dispTokensOut || dispCalls || dispCost;

  const setFilter = (provider, model) => {
    host._usageFilter = { provider, model };
    host._usageFilterUserSet = true;
    // Re-fetch filtered totals from the store. loadUsageStats reads
    // host._usageFilter so we just call it.
    if (typeof host._loadUsageStats === "function") {
      host._loadUsageStats();
    }
    host.requestUpdate();
  };

  const filterChips =
    providerOptions.length === 0
      ? ""
      : html`
          <div class="usage-snippet-pills" style="margin-bottom:12px;">
            <button
              class="usage-snippet-pill ${!filter.provider ? "active" : ""}"
              @click=${() => setFilter(null, null)}
            >
              All providers
            </button>
            ${providerOptions.map(
              (p) => html`
                <button
                  class="usage-snippet-pill ${filter.provider === p &&
                  filter.model == null
                    ? "active"
                    : ""}"
                  @click=${() => setFilter(p, null)}
                >
                  ${_providerLabel(p)}
                </button>
              `,
            )}
          </div>
          ${filter.provider && modelOptions.length > 1
            ? html`
                <div class="usage-snippet-pills" style="margin-bottom:12px;">
                  <button
                    class="usage-snippet-pill ${filter.model == null
                      ? "active"
                      : ""}"
                    @click=${() => setFilter(filter.provider, null)}
                  >
                    All models
                  </button>
                  ${modelOptions.map(
                    (m) => html`
                      <button
                        class="usage-snippet-pill ${filter.model === m
                          ? "active"
                          : ""}"
                        @click=${() => setFilter(filter.provider, m)}
                      >
                        ${m || "(no model)"}
                      </button>
                    `,
                  )}
                </div>
              `
            : ""}
        `;

  return html`
    <div class="scroll-view">
      <div class="usage-view">
        <a
          class="usage-crumb"
          href="#"
          @click=${(e) => {
            e.preventDefault();
            host._activeTab = "settings";
            host.requestUpdate();
          }}
        >
          <ha-icon icon="mdi:chevron-left"></ha-icon>
          <span>Back to settings</span>
        </a>
        <div class="usage-title-row">
          <h2>Token usage</h2>
          ${lastProvider
            ? html`
                <span class="usage-subtitle">
                  ${lastProvider}${lastModel ? ` · ${lastModel}` : ""}
                </span>
              `
            : ""}
        </div>

        ${filterChips}
        ${sensorsMissing && recent !== null && recent.length === 0
          ? html`
              <div class="section-card usage-empty">
                <ha-icon
                  icon="mdi:information-outline"
                  style="--mdc-icon-size:20px;"
                ></ha-icon>
                <div>
                  <strong>No usage data yet.</strong>
                  <p>
                    Usage will appear after the first LLM call. Try chatting
                    with Selora AI or running a suggestion cycle. If you've
                    already used Selora AI and still see this, restart Home
                    Assistant so the new sensors get registered.
                  </p>
                </div>
              </div>
            `
          : html`
              ${hasTotals
                ? html`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Totals</h3>
                      </div>
                      <div class="usage-tile-grid">
                        ${_renderTile({
                          label: "Cost",
                          value: _fmtUsd(dispCost),
                          sub: "USD estimate",
                          icon: "mdi:cash",
                        })}
                        ${_renderTile({
                          label: "Calls",
                          value: _fmtInt(dispCalls),
                          icon: "mdi:counter",
                        })}
                        ${_renderTile({
                          label: "Tokens in",
                          value: _fmtTokens(dispTokensIn),
                          icon: "mdi:upload",
                        })}
                        ${_renderTile({
                          label: "Tokens out",
                          value: _fmtTokens(dispTokensOut),
                          icon: "mdi:download",
                        })}
                      </div>
                    </div>
                  `
                : ""}
              ${!sensorsMissing || filterActive
                ? html`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>By period</h3>
                      </div>
                      ${_renderPeriodRow("Today", periodStats?.today)}
                      ${_renderPeriodRow("Last 7 days", periodStats?.week)}
                      ${_renderPeriodRow("This month", periodStats?.month)}
                      <div class="usage-period-note">
                        ${filterActive
                          ? "Period buckets come from the integration's usage store (kept for 30 days)."
                          : "Period buckets come from Home Assistant's long-term statistics, which compile hourly. New activity may take up to an hour to appear here."}
                      </div>
                    </div>
                  `
                : ""}

              <div class="section-card">
                <div class="section-card-header">
                  <h3>Where tokens go</h3>
                  <span class="usage-section-sub">
                    Last
                    ${filteredRecent === null ? "…" : filteredRecent.length}
                    call${filteredRecent && filteredRecent.length === 1
                      ? ""
                      : "s"}
                    · resets on HA restart
                  </span>
                </div>
                ${filteredRecent && filteredRecent.length > 0
                  ? html`
                      <div
                        class="usage-snippet-pills"
                        style="margin-bottom:12px;"
                      >
                        <button
                          class="usage-snippet-pill ${groupingMode === "kind"
                            ? "active"
                            : ""}"
                          @click=${() => {
                            host._usageGrouping = "kind";
                            host.requestUpdate();
                          }}
                        >
                          By kind
                        </button>
                        <button
                          class="usage-snippet-pill ${groupingMode ===
                          "provider"
                            ? "active"
                            : ""}"
                          @click=${() => {
                            host._usageGrouping = "provider";
                            host.requestUpdate();
                          }}
                        >
                          By provider
                        </button>
                      </div>
                    `
                  : ""}
                ${filteredRecent === null
                  ? html`<div class="usage-period-loading">Loading…</div>`
                  : filteredRecent.length === 0
                    ? html`<div class="usage-period-empty">
                        No calls recorded yet.
                      </div>`
                    : _renderBreakdown(breakdown, totalCost)}
              </div>

              ${filteredRecent && filteredRecent.length > 0
                ? html`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Recent calls</h3>
                      </div>
                      ${_renderRecentList(filteredRecent.slice(-15).reverse())}
                    </div>
                  `
                : ""}
              ${_renderPricingCard(host)}
              ${sensorsMissing
                ? html`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Dashboard sensors</h3>
                      </div>
                      <p class="usage-help">
                        Restart Home Assistant to register the usage sensors.
                        Once registered, you can add them to any dashboard with
                        a
                        <code>statistics-graph</code> card.
                      </p>
                    </div>
                  `
                : html`
                    <div class="section-card">
                      <div class="section-card-header">
                        <h3>Add to your dashboard</h3>
                      </div>
                      <p class="usage-help">
                        Each metric has a different scale — create one card per
                        sensor. Pick a metric, copy the YAML, then paste it in a
                        dashboard's YAML editor.
                      </p>
                      ${_renderDashboardSnippet(host, sensors)}
                    </div>
                  `}
            `}
      </div>
    </div>
  `;
}
