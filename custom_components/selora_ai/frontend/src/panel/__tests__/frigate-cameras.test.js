import { describe, expect, it, vi } from "vitest";

import { resolveClientActions } from "../client-actions.js";
import { renderFrigateCameraRows } from "../frigate-cameras.js";

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

// Every @input handler in a template, in document order.
function inputs(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => inputs(n, out));
  else if (typeof node === "function") out.push(node);
  else if (node && typeof node === "object" && "values" in node) {
    inputs(node.values, out);
  }
  return out;
}

const action = (planId) => ({
  kind: "add_frigate_cameras",
  plan_id: planId,
  objects: ["person"],
  record: true,
  record_days: 7,
  skipped: [{ name: "Doorbell", reason: "it streams from the internet" }],
  cameras: [
    {
      key: "porch",
      source: "home_assistant",
      name: "Porch",
      area: "Front",
      record_stream: "rtsp://192.168.1.20/main",
    },
    {
      key: "192.168.1.60",
      source: "network",
      name: "Garage Cam",
      address: "192.168.1.60",
      brand: "reolink",
      needs_login: true,
    },
  ],
});

const host = (callWS) => ({
  hass: { callWS: vi.fn(callWS) },
  _activeSessionId: "sess-1",
  _messages: [],
  _openSession: vi.fn(async () => {}),
  _sendMessage: vi.fn(async () => {}),
  requestUpdate: vi.fn(),
  _t: (_key, fallback) => fallback,
});

describe("the Frigate cameras card", () => {
  it("asks for a login only for cameras found on the network", () => {
    const out = flatten(renderFrigateCameraRows(host(), action("plan-a")));

    expect(out).toContain("Porch");
    expect(out).toContain("192.168.1.60 · reolink");
    expect(out.match(/type=/g)).toHaveLength(2); // one username, one password
    expect(out).toContain("Detects person; recordings kept 7 days.");
    expect(out).toContain(
      "Not added: Doorbell (it streams from the internet).",
    );
  });

  it("sends only the plan id and the logins typed in", async () => {
    const h = host(async () => ({ status: "added", cameras: ["Garage Cam"] }));
    const card = action("plan-b");
    const [username, password] = inputs(renderFrigateCameraRows(h, card));
    username({ target: { value: "viewer" } });
    password({ target: { value: "s3cret" } });

    await resolveClientActions(
      h,
      {},
      {
        proposal_id: "p1",
        client_actions: [card],
      },
    );

    const add = h.hass.callWS.mock.calls.find(
      (c) => c[0].type === "selora_ai/frigate/add_cameras",
    );
    expect(add[0]).toEqual({
      type: "selora_ai/frigate/add_cameras",
      plan_id: "plan-b",
      credentials: {
        "192.168.1.60": { username: "viewer", password: "s3cret" },
      },
    });
    const report = h.hass.callWS.mock.calls.find(
      (c) => c[0].type === "selora_ai/client_action_result",
    );
    expect(JSON.stringify(report[0])).not.toContain("s3cret");
    // Adding the cameras was the whole request: no replay of it.
    expect(h._sendMessage).not.toHaveBeenCalled();
  });

  it("continues when the model declared something left to do", async () => {
    const h = host(async () => ({ status: "added", cameras: ["Porch"] }));

    await resolveClientActions(
      h,
      {},
      {
        proposal_id: "p3",
        remaining_intent: "put it on the dashboard",
        client_actions: [action("plan-d")],
      },
    );

    expect(h._sendMessage).toHaveBeenCalledWith({ resumeProposalId: "p3" });
  });

  it("keeps the card open and names the camera when its login is refused", async () => {
    const h = host(async () => ({
      error: "Some cameras could not be read.",
      camera_errors: {
        "192.168.1.60": "The camera refused this username or password.",
      },
    }));
    const msg = { approval_status: "pending" };
    const card = action("plan-c");

    await resolveClientActions(h, msg, {
      proposal_id: "p2",
      client_actions: [card],
    });

    expect(msg.approval_status).toBe("pending");
    expect(
      h.hass.callWS.mock.calls.some(
        (c) => c[0].type === "selora_ai/client_action_result",
      ),
    ).toBe(false);
    expect(flatten(renderFrigateCameraRows(h, card))).toContain(
      "The camera refused this username or password.",
    );
  });
});
