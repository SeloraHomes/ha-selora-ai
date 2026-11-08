import { describe, expect, it, vi } from "vitest";

import { runClientAction } from "../client-actions.js";
import { renderIntegrationSetupRow } from "../integration-setup.js";

function flatten(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(flatten).join("");
  if (typeof node === "object" && node.strings && "values" in node) {
    let out = "";
    node.strings.forEach((s, i) => {
      out += s;
      if (i < node.values.length) out += flatten(node.values[i]);
    });
    return out;
  }
  if (typeof node === "function") return "";
  return String(node);
}

// Every event handler in a template, in document order.
function handlers(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => handlers(n, out));
  else if (typeof node === "function") out.push(node);
  else if (node && typeof node === "object" && "values" in node) {
    handlers(node.values, out);
  }
  return out;
}

const TEXTS = {
  "component.hue.config.step.user.title": "Sign in to {name}",
  "component.hue.config.step.user.data.username": "Username",
  "component.hue.config.step.user.data.password": "Password",
  "component.hue.config.error.invalid_auth": "Invalid login.",
  "component.hue.config.abort.cannot_connect": "Failed to connect.",
};

const LOGIN = {
  type: "form",
  flow_id: "f1",
  step_id: "user",
  description_placeholders: { name: "Hue Bridge" },
  data_schema: [
    { name: "username", type: "string", required: true },
    { name: "password", type: "string", required: true },
  ],
};

let n = 0;
function setup(responses) {
  const flowId = `flow-${++n}`;
  const callApi = vi.fn(async (method) =>
    method === "GET" ? { ...LOGIN, flow_id: flowId } : responses.shift(),
  );
  const host = {
    hass: {
      language: "en",
      callApi,
      callWS: vi.fn(async () => ({ resources: TEXTS })),
    },
    _t: (_key, fallback) => fallback,
    requestUpdate: vi.fn(),
  };
  const action = {
    kind: "integration_setup",
    flow_id: flowId,
    domain: "hue",
    label: "Philips Hue",
  };
  return { host, action, callApi };
}

async function loaded(host, action) {
  renderIntegrationSetupRow(host, action);
  await vi.waitFor(() => expect(host.requestUpdate).toHaveBeenCalled());
  return renderIntegrationSetupRow(host, action);
}

describe("integration setup card", () => {
  it("shows the step in the integration's own words, the password masked", async () => {
    const { host, action } = setup([]);
    const out = flatten(await loaded(host, action));
    expect(out).toContain("Sign in to Hue Bridge");
    expect(out).toContain("Username");
    expect(out).toMatch(/type=\s*"?password/);
  });

  it("posts what was typed to Home Assistant's flow, and reports only the entry", async () => {
    const { host, action, callApi } = setup([
      {
        type: "create_entry",
        title: "Hue Bridge",
        result: { entry_id: "e1" },
      },
    ]);
    const row = await loaded(host, action);
    const [setUser, setPassword] = handlers(row);
    setUser({ target: { value: "me" } });
    setPassword({ target: { value: "hunter2" } });

    const result = await runClientAction(host.hass, action);

    expect(callApi).toHaveBeenLastCalledWith(
      "POST",
      `config/config_entries/flow/${action.flow_id}`,
      { username: "me", password: "hunter2" },
    );
    expect(result).toEqual({
      ok: true,
      kind: "integration_setup",
      detail: { entry_id: "e1", title: "Hue Bridge" },
    });
    expect(JSON.stringify(result)).not.toContain("hunter2");
  });

  it("keeps the card open on a refused login, saying why", async () => {
    const { host, action } = setup([
      { ...LOGIN, errors: { base: "invalid_auth" } },
    ]);
    await loaded(host, action);

    const result = await runClientAction(host.hass, action);

    expect(result.retry).toBe(true);
    expect(flatten(renderIntegrationSetupRow(host, action))).toContain(
      "Invalid login.",
    );
  });

  it("reports a setup Home Assistant stopped, in its words", async () => {
    const { host, action } = setup([
      { type: "abort", reason: "cannot_connect" },
    ]);
    await loaded(host, action);

    const result = await runClientAction(host.hass, action);

    expect(result).toMatchObject({ ok: false, detail: "Failed to connect." });
  });

  it("opens a sign-in in the browser, then reads where the flow is", async () => {
    const open = vi.fn();
    vi.stubGlobal("window", { open });
    const { host, action, callApi } = setup([
      {
        type: "external",
        flow_id: "x",
        step_id: "auth",
        url: "https://id.example/auth",
      },
    ]);
    await loaded(host, action);

    expect((await runClientAction(host.hass, action)).retry).toBe(true);
    expect(open).toHaveBeenCalledWith(
      "https://id.example/auth",
      "_blank",
      "noopener",
    );
    expect(flatten(renderIntegrationSetupRow(host, action))).toContain(
      "Open the sign-in page",
    );

    await runClientAction(host.hass, action);
    expect(callApi).toHaveBeenLastCalledWith(
      "GET",
      `config/config_entries/flow/${action.flow_id}`,
    );
    vi.unstubAllGlobals();
  });

  it("says when the setup is no longer open", async () => {
    const { host, action, callApi } = setup([]);
    callApi.mockRejectedValue({ status_code: 404 });
    const out = flatten(await loaded(host, action));
    expect(out).toContain("This setup is no longer open.");
  });
});

describe("a step asking the user to act on the device", () => {
  const DISABLED = {
    type: "form",
    flow_id: "old",
    step_id: "protocol_disabled",
    description_placeholders: { protocol: "AirPlay" },
    data_schema: [],
  };

  const ADDED = {
    type: "create_entry",
    title: "Kitchen",
    result: { entry_id: "e9" },
  };

  // `continued`: what Continue on the step answers.
  function deviceStep(continued = ADDED) {
    const calls = [];
    const callApi = vi.fn(async (method, path, data) => {
      calls.push([method, path, data]);
      if (method === "GET") return DISABLED;
      if (method === "POST" && path === "config/config_entries/flow") {
        return {
          ...LOGIN,
          flow_id: "fresh",
          step_id: "user",
          data_schema: [{ name: "device", type: "string", required: true }],
        };
      }
      return path.endsWith("/fresh") ? ADDED : continued;
    });
    const host = {
      hass: {
        language: "en",
        callApi,
        callWS: vi.fn(async () => ({
          resources: {
            "component.apple_tv.config.step.protocol_disabled.description":
              "Pairing is required for `{protocol}` but it is disabled on the device.",
          },
        })),
      },
      _t: (_key, fallback) => fallback,
      requestUpdate: vi.fn(),
    };
    const action = {
      kind: "integration_setup",
      flow_id: `card-${++n}`,
      domain: "apple_tv",
      label: "Apple TV",
      steps: ["Open the Home app", "Allow Speaker & TV Access: Everyone"],
    };
    return { host, action, calls };
  }

  it("shows Selora's steps for the device, and Continue retries them", async () => {
    const { host, action } = deviceStep();
    const out = flatten(await loaded(host, action));
    expect(out).toContain("Open the Home app");
    expect(out).toContain("Allow Speaker & TV Access: Everyone");
    expect(out).toContain("<code>AirPlay</code>");
    // A TV's Allow prompt comes back on Continue; starting over would lose
    // the address its discovery found.
    expect(out).not.toContain("Check again");
    expect(await runClientAction(host.hass, action)).toMatchObject({
      ok: true,
    });
  });

  it("offers to check again when Continue leaves the step as it was", async () => {
    const { host, action } = deviceStep(DISABLED);
    await loaded(host, action);

    expect(await runClientAction(host.hass, action)).toMatchObject({
      retry: true,
    });

    expect(flatten(renderIntegrationSetupRow(host, action))).toContain(
      "Check again",
    );
  });

  it("keeps the card, with the reason, when Continue ends the setup", async () => {
    const { host, action } = deviceStep({
      type: "abort",
      reason: "setup_failed",
    });
    await loaded(host, action);

    expect(await runClientAction(host.hass, action)).toMatchObject({
      retry: true,
    });

    const out = flatten(renderIntegrationSetupRow(host, action));
    expect(out).toContain("setup_failed");
    expect(out).toContain("Check again");
  });

  it("checks again by ending the setup and starting a fresh one", async () => {
    const { host, action, calls } = deviceStep(DISABLED);
    await loaded(host, action);
    await runClientAction(host.hass, action);
    const checkAgain = handlers(renderIntegrationSetupRow(host, action)).at(-1);

    await checkAgain();

    expect(calls).toContainEqual([
      "DELETE",
      `config/config_entries/flow/${action.flow_id}`,
      undefined,
    ]);
    expect(calls).toContainEqual([
      "POST",
      "config/config_entries/flow",
      { handler: "apple_tv", show_advanced_options: false },
    ]);
    // The fresh setup's step is shown, and answered on its own flow.
    expect(flatten(renderIntegrationSetupRow(host, action))).not.toContain(
      "Check again",
    );
    await runClientAction(host.hass, action);
    expect(calls.at(-1).slice(0, 2)).toEqual([
      "POST",
      "config/config_entries/flow/fresh",
    ]);
  });
});
