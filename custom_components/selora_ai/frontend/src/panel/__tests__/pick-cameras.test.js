import { describe, expect, it, vi } from "vitest";

import { _resolveApproval } from "../chat-actions.js";
import { pickedTargets, renderApprovalCard } from "../render-approval-card.js";

function ser(value) {
  if (value == null || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number")
    return String(value);
  if (typeof value === "function") return "[fn]";
  if (Array.isArray(value)) return value.map(ser).join("");
  if (value.strings) {
    let out = "";
    for (let i = 0; i < value.strings.length; i++) {
      out += value.strings[i];
      if (i < value.values.length) out += ser(value.values[i]);
    }
    return out;
  }
  return "[obj]";
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

const camera = (name) => ({
  kind: "frigate_objects",
  verb: "set_objects",
  target_id: name,
  name,
  label: `${name} starts looking for dog (Selora Vision restarts)`,
});

function card(pickable = true) {
  return {
    role: "assistant",
    content: "",
    approval_status: "pending",
    command_approval: {
      proposal_id: "p1",
      approval_kind: "destructive",
      calls: [],
      deletes: [],
      removes: false,
      actions: [camera("e1_zoom"), camera("wyze_front")],
      ...(pickable ? { pickable: true } : {}),
    },
  };
}

function host(msg) {
  return {
    _t: (_key, fallback) => fallback,
    _messages: [msg],
    _activeSessionId: "s1",
    requestUpdate: vi.fn(),
    _showToast: vi.fn(),
    hass: {
      language: "en",
      callWS: vi.fn(async () => ({ status: "approved" })),
    },
  };
}

const render = (h, msg) =>
  renderApprovalCard(h, msg, msg.command_approval, msg.approval_status);

describe("a card that changes several cameras", () => {
  it("ticks every camera to start with", () => {
    const msg = card();
    const markup = ser(render(host(msg), msg));
    expect(markup.match(/type="checkbox"/g)).toHaveLength(2);
    expect(pickedTargets(msg)).toEqual(["e1_zoom", "wyze_front"]);
  });

  it("applies only the cameras left ticked", async () => {
    const msg = card();
    const h = host(msg);
    const [untickE1] = handlers(render(h, msg));
    untickE1();
    expect(pickedTargets(msg)).toEqual(["wyze_front"]);

    await _resolveApproval.call(h, msg, "apply", "p1");

    expect(h.hass.callWS).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "apply", targets: ["wyze_front"] }),
    );
  });

  it("applies nothing with every camera unticked, and says why", async () => {
    const msg = card();
    msg._unpicked = ["e1_zoom", "wyze_front"];
    const h = host(msg);

    await _resolveApproval.call(h, msg, "apply", "p1");

    expect(h.hass.callWS).not.toHaveBeenCalled();
    expect(h._showToast).toHaveBeenCalledWith(
      "Tick at least one camera to apply.",
      "error",
    );
    expect(msg.approval_status).toBe("pending");
  });

  it("sends no pick for a card that does not offer one", async () => {
    const msg = card(false);
    const h = host(msg);
    expect(ser(render(h, msg))).not.toContain('type="checkbox"');

    await _resolveApproval.call(h, msg, "apply", "p1");

    expect(h.hass.callWS.mock.calls[0][0]).not.toHaveProperty("targets");
  });
});
