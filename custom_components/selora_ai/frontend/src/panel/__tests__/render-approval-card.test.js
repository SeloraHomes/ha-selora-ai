import { describe, expect, it } from "vitest";

import { renderApprovalCard } from "../render-approval-card.js";

// This file had no rendering tests at all, which is how a whole card shipped
// unstyled and then as a duplicate of the one next to it. Serializing the lit
// template is enough to see the accent, the copy, the rows and the button.
const host = { _t: (key, fallback) => fallback };

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

const render = (approval, status) =>
  ser(renderApprovalCard(host, {}, approval, status)).replace(/\s+/g, " ");

const DELETE = {
  approval_kind: "delete",
  deletes: [{ kind: "scene", label: "Movie Night", entity_id: "scene.movie" }],
};
const CLIENT = {
  approval_kind: "client_action",
  client_actions: [
    { kind: "create_dashboard", title: "Office", url_path: "office" },
  ],
};

describe("the shared confirmation card", () => {
  it("renders a deletion with the destructive accent and its own copy", () => {
    const out = render(DELETE);
    expect(out).toContain("Delete this?");
    expect(out).toContain("Movie Night");
    expect(out).toContain("#ef4444");
    expect(out).toContain("This permanently removes it and can't be undone.");
  });

  it("switches to neutral copy when the card is not purely deletions", () => {
    const out = render({
      ...DELETE,
      actions: [{ kind: "area", label: "Study" }],
    });
    expect(out).toContain("Apply these changes?");
    expect(out).not.toContain("Delete this?");
    // Both rows are shown — a change the user was not shown cannot be refused.
    expect(out).toContain("Movie Night");
    expect(out).toContain("Study");
  });

  it("renders a client action on the same card, in Selora's accent", () => {
    const out = render(CLIENT);
    expect(out).toContain("Needs your confirmation");
    expect(out).toContain("Create the Office dashboard at /office");
    // Not the destructive red: nothing here is destructive.
    expect(out).toContain("var(--selora-accent)");
    expect(out).not.toContain("#ef4444");
  });

  it("gives the client action a chip and the others none", () => {
    // The others resolve server-side and get Allow/Deny from quick_actions —
    // and this one uses that same chip, quiet tone included, rather than a
    // button from the generic family.
    const out = render(CLIENT);
    expect(out).toContain("qa-confirm");
    expect(out).toContain("qa-confirm--approve");
    expect(out).not.toContain("btn-primary");
    expect(render(DELETE)).not.toContain("<button");
  });

  it("shows the plan on any variant that declared one", () => {
    // A deletion is often a step — "delete the old scene and rebuild it" — so
    // the card has to show what follows, or the user approves the deletion
    // believing that is the whole of it.
    const withPlan = (approval) =>
      render({ ...approval, remaining_intent: "rebuild it for winter" });

    for (const approval of [DELETE, CLIENT]) {
      const out = withPlan(approval);
      expect(out).toContain("then");
      expect(out).toContain("rebuild it for winter");
    }
    // And nothing extra when there is no plan.
    expect(render(DELETE)).not.toContain("mdi:arrow-right-bottom");
  });

  it("reports each terminal state with the variant's own wording", () => {
    expect(render(DELETE, "approved")).toContain("Deleted");
    expect(render(DELETE, "denied")).toContain("Cancelled");
    expect(render(CLIENT, "approved")).toContain("Done.");
    expect(render(CLIENT, "denied")).toContain("That did not work.");
    expect(render(CLIENT, "resolving")).toContain("Working…");
  });

  it("falls through to the service-call card for a risk-level approval", () => {
    const out = render({
      risk_level: "high",
      calls: [{ service: "lock.unlock" }],
    });
    expect(out).toContain("#ef4444");
    expect(out).not.toContain("Needs your confirmation");
  });
});

describe("no card shows the same icon twice", () => {
  // The head icon says what the CARD is; the row icon says what the thing is.
  // Setting both to the dashboard glyph rendered it twice in a row, which
  // reads as a repeated element rather than two pieces of information.
  const icons = (approval) =>
    [...render(approval).matchAll(/icon=([\w:-]+|"[^"]*")/g)].map((m) =>
      m[1].replace(/"/g, ""),
    );

  it("head and row icons differ on a client action", () => {
    const [head, row] = icons(CLIENT);
    expect(head).toBeTruthy();
    expect(row).toBeTruthy();
    expect(head).not.toBe(row);
  });

  it("head and row icons differ on a deletion", () => {
    const [head, row] = icons(DELETE);
    expect(head).not.toBe(row);
  });
});

describe("the card follows the action, not the kind of card", () => {
  const DELETE = {
    approval_kind: "client_action",
    client_actions: [
      {
        kind: "delete_dashboard",
        url_path: "office",
        title: "Office",
        view_count: 2,
        card_count: 5,
      },
    ],
  };

  it("says Delete, not Create, on a card that deletes", () => {
    // "Create" on a button that removes a dashboard would be worse than
    // unhelpful — the wording and tone follow the ACTION.
    const out = render(DELETE);
    expect(out).toContain("Delete");
    expect(out).not.toContain(">Create<");
    expect(out).toContain("qa-confirm--deny");
    expect(out).not.toContain("qa-confirm--approve");
  });

  it("names what goes with it", () => {
    const out = render(DELETE);
    expect(out).toContain("Office");
    expect(out).toContain("2");
    expect(out).toContain("5");
  });

  it("still says Create on a creation", () => {
    const out = render(CLIENT);
    expect(out).toContain("Create");
    expect(out).toContain("qa-confirm--approve");
  });
});

describe("a card carrying more than one action", () => {
  const mixed = (kinds) => ({
    approval_kind: "client_action",
    client_actions: kinds.map((kind) => ({
      kind,
      url_path: "office",
      title: "Office",
      view_count: 1,
      card_count: 1,
    })),
  });

  it("is neutral and destructive when it both creates and deletes", () => {
    // One tool round can propose both, and they share a single button. Taking
    // the wording from the first row would put an approve-toned "Create" on a
    // press that also deletes a dashboard.
    for (const order of [
      ["create_dashboard", "delete_dashboard"],
      ["delete_dashboard", "create_dashboard"],
    ]) {
      const out = render(mixed(order));
      expect(out).toContain("Confirm all");
      expect(out).toContain("qa-confirm--deny");
      expect(out).not.toContain(">Create<");
    }
  });

  it("keeps the specific wording when every action agrees", () => {
    expect(render(mixed(["delete_dashboard", "delete_dashboard"]))).toContain(
      "Delete",
    );
    expect(render(mixed(["create_dashboard", "create_dashboard"]))).toContain(
      "Create",
    );
  });
});

describe("the delete card's blast radius", () => {
  const withCounts = (extra) => ({
    approval_kind: "client_action",
    client_actions: [
      {
        kind: "delete_dashboard",
        url_path: "office",
        title: "Office",
        ...extra,
      },
    ],
  });

  it("says unknown rather than zero when the document cannot be read", () => {
    // A generated Overview renders content nothing can enumerate. "0 views, 0
    // cards" on an irreversible delete is a false blast radius, and a false
    // one invites the tap.
    const out = render(withCounts({}));
    expect(out).toContain("contents unknown");
    expect(out).not.toContain("0 views");
  });

  it("names the counts when it can read them", () => {
    const out = render(withCounts({ view_count: 2, card_count: 7 }));
    expect(out).toContain("2");
    expect(out).toContain("7");
    expect(out).not.toContain("contents unknown");
  });

  it("does not mistake a genuinely empty dashboard for an unknown one", () => {
    const out = render(withCounts({ view_count: 0, card_count: 0 }));
    expect(out).not.toContain("contents unknown");
  });
});
