import { describe, it, expect, vi } from "vitest";
import { renderHomeSuggestions } from "../home-suggestions.js";
import { messageDetails, stripEntityMarkers } from "../chat-autocomplete.js";

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
  // A directive (keyed) renders its last value.
  if (typeof node === "object" && Array.isArray(node.values)) {
    return flatten(node.values.at(-1));
  }
  if (typeof node === "function") return "";
  return String(node);
}

// Every event handler in a template, in document order: a row's toggle,
// snooze and dismiss, then the open row's "Ask Selora to do it", then
// "Other ideas".
function handlers(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => handlers(n, out));
  else if (typeof node === "function") out.push(node);
  else if (node && typeof node === "object" && "values" in node) {
    handlers(node.values, out);
  }
  return out;
}

const leak = {
  suggestion_id: "add:group:binary_sensor.leaks:binary_sensor.leak_new",
  type: "add_to_collection",
  entities: [{ entity_id: "binary_sensor.leak_new", name: "Leak kitchen" }],
  collection: {
    source: "group_helper",
    name: "Water Leak Sensors",
    ref: "binary_sensor.leaks",
  },
  members: [
    { entity_id: "binary_sensor.leak_a", name: "Leak bath" },
    { entity_id: "binary_sensor.leak_b", name: "Leak laundry" },
  ],
  area: null,
};

const host = (suggestions) => ({
  hass: { language: "en" },
  _homeSuggestions: suggestions,
  _t: (_key, fallback) => fallback,
  _askInNewChat: vi.fn(),
});

describe("renderHomeSuggestions", () => {
  it("renders nothing without suggestions", () => {
    expect(renderHomeSuggestions(host([]))).toBe("");
  });

  const starters = [
    {
      label: "What devices do I have?",
      value: "List my devices",
      icon: "mdi:devices",
    },
  ];

  it("offers the starters when the home has nothing to suggest", () => {
    const h = { ...host([]), _quickStart: vi.fn() };
    const out = renderHomeSuggestions(h, starters);
    expect(flatten(out)).toContain("Try asking");
    expect(flatten(out)).toContain("What devices do I have?");

    handlers(out)[0]();
    expect(h._quickStart).toHaveBeenCalledWith("List my devices");
  });

  it("shows only the home's suggestions when it has some", () => {
    const out = flatten(renderHomeSuggestions(host([leak]), starters));
    expect(out).toContain("Add Leak kitchen to Water Leak Sensors");
    expect(out).not.toContain("What devices do I have?");
  });

  it("animates the list in only when it arrives after the chat home", () => {
    const shown = host([leak]);
    expect(flatten(renderHomeSuggestions(shown))).not.toContain("entering");

    const late = host(undefined);
    renderHomeSuggestions(late, starters);
    late._homeSuggestions = [leak];
    expect(flatten(renderHomeSuggestions(late))).toContain("entering");
  });

  it("waits for the suggestions before offering the starters", () => {
    expect(renderHomeSuggestions(host(undefined), starters)).toBe("");
  });

  it("words an addition to a group", () => {
    const out = flatten(renderHomeSuggestions(host([leak])));
    expect(out).toContain("Add Leak kitchen to Water Leak Sensors");
  });

  it("opens to say why, and only then asks in a new chat", () => {
    const h = host([leak]);
    const [toggle] = handlers(renderHomeSuggestions(h));
    expect(flatten(renderHomeSuggestions(h))).not.toContain("already covers");

    toggle();
    const opened = renderHomeSuggestions(h);
    expect(h._askInNewChat).not.toHaveBeenCalled();
    expect(flatten(opened)).toContain(
      "Water Leak Sensors already covers Leak bath and Leak laundry: every one of this kind in your home.",
    );

    handlers(opened).at(-1)();
    const [sent, opts] = h._askInNewChat.mock.calls[0];
    expect(opts.guide).toBeUndefined();
    // The bubble shows the title; the model also gets the request with ids.
    expect(stripEntityMarkers(sent)).toBe(
      "Add Leak kitchen to Water Leak Sensors",
    );
    expect(messageDetails(sent)).toBe(
      "Add Leak kitchen (binary_sensor.leak_new) to the group Water Leak Sensors (binary_sensor.leaks).",
    );
  });

  it("opens an automation for editing before asking", async () => {
    const h = host([
      {
        ...leak,
        collection: {
          source: "automation",
          name: "Water Leak Alert",
          ref: "automation.water_leak_alert",
          automation_id: "leak_alert",
        },
      },
    ]);
    h.hass.callWS = vi.fn(async () => ({ session_id: "s1" }));
    h._setActiveTab = vi.fn();
    h._openSession = vi.fn(async (id) => {
      h._activeSessionId = id;
    });
    h._sendMessage = vi.fn();
    handlers(renderHomeSuggestions(h))[0]();

    await handlers(renderHomeSuggestions(h)).at(-1)();

    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/load_automation_to_session",
      automation_id: "leak_alert",
    });
    expect(h._openSession).toHaveBeenCalledWith("s1");
    expect(stripEntityMarkers(h._input)).toBe(
      "Include Leak kitchen in Water Leak Alert",
    );
    expect(h._sendMessage).toHaveBeenCalled();
    expect(h._askInNewChat).not.toHaveBeenCalled();
  });

  it("sends nothing when the automation's chat fails to open", async () => {
    const h = host([
      {
        ...leak,
        collection: {
          ...leak.collection,
          source: "automation",
          automation_id: "a",
        },
      },
    ]);
    h._activeSessionId = "previous";
    h.hass.callWS = vi.fn(async () => ({ session_id: "s1" }));
    h._setActiveTab = vi.fn();
    h._openSession = vi.fn(async () => {});
    h._sendMessage = vi.fn();
    handlers(renderHomeSuggestions(h))[0]();

    await handlers(renderHomeSuggestions(h)).at(-1)();

    expect(h._sendMessage).not.toHaveBeenCalled();
  });

  it("names the room when the collection covers one", () => {
    const h = host([{ ...leak, area: "Basement" }]);
    handlers(renderHomeSuggestions(h))[0]();
    expect(flatten(renderHomeSuggestions(h))).toContain(
      "every one of this kind in Basement.",
    );
  });

  it("words grouping an automation's triggers", () => {
    const s = {
      ...leak,
      type: "group_entities",
      entities: [1, 2, 3].map((n) => ({
        entity_id: `binary_sensor.leak_${n}`,
        name: `Leak ${n}`,
      })),
      collection: { source: "automation", name: "Water Leak Alert", ref: "x" },
    };
    const out = flatten(renderHomeSuggestions(host([s])));
    expect(out).toContain("Group the 3 devices that trigger Water Leak Alert");
  });

  it("skips a type this bundle does not know, and shows at most three", () => {
    const many = [
      { ...leak, type: "from_the_future" },
      ...[1, 2, 3, 4].map((n) => ({ ...leak, suggestion_id: `s${n}` })),
    ];
    const out = flatten(renderHomeSuggestions(host(many)));
    expect(out.split("home-suggestion-main").length - 1).toBe(3);
  });

  const numbered = (count) =>
    Array.from({ length: count }, (_, n) => ({
      ...leak,
      suggestion_id: `s${n + 1}`,
      entities: [
        { entity_id: `binary_sensor.leak_${n + 1}`, name: `Leak ${n + 1}` },
      ],
    }));
  const titles = (out) =>
    [...flatten(out).matchAll(/Add (Leak \d+) to/g)].map((m) => m[1]);

  it("pages through the rest three at a time, and wraps around", () => {
    const h = host(numbered(7));
    expect(titles(renderHomeSuggestions(h))).toEqual([
      "Leak 1",
      "Leak 2",
      "Leak 3",
    ]);

    handlers(renderHomeSuggestions(h)).at(-1)();
    expect(titles(renderHomeSuggestions(h))).toEqual([
      "Leak 4",
      "Leak 5",
      "Leak 6",
    ]);
    handlers(renderHomeSuggestions(h)).at(-1)();
    expect(titles(renderHomeSuggestions(h))).toEqual(["Leak 7"]);
    handlers(renderHomeSuggestions(h)).at(-1)();
    expect(titles(renderHomeSuggestions(h))).toEqual([
      "Leak 1",
      "Leak 2",
      "Leak 3",
    ]);
  });

  it("offers other ideas only when there are more than three", () => {
    expect(flatten(renderHomeSuggestions(host(numbered(3))))).not.toContain(
      "Other ideas",
    );
    expect(flatten(renderHomeSuggestions(host(numbered(4))))).toContain(
      "Other ideas",
    );
  });

  it("shows the page before when the last one empties", () => {
    const h = { ...host(numbered(4)), _homeSuggestionPage: 1 };
    h._homeSuggestions = numbered(3);
    expect(titles(renderHomeSuggestions(h))).toEqual([
      "Leak 1",
      "Leak 2",
      "Leak 3",
    ]);
  });

  it("turns from the page on screen, not a page that emptied", () => {
    const h = { ...host(numbered(10)), _homeSuggestionPage: 3 };
    h._homeSuggestions = numbered(9);
    expect(titles(renderHomeSuggestions(h))).toEqual([
      "Leak 7",
      "Leak 8",
      "Leak 9",
    ]);
    handlers(renderHomeSuggestions(h)).at(-1)();
    expect(titles(renderHomeSuggestions(h))).toEqual([
      "Leak 1",
      "Leak 2",
      "Leak 3",
    ]);
  });

  it("reads the list again once a row is put away, for the next in line", async () => {
    const h = host(numbered(1));
    const next = { ...leak, suggestion_id: "discovered:next" };
    h.hass.callWS = vi.fn(async ({ type }) =>
      type === "selora_ai/home_suggestions" ? [next] : { snoozed: true },
    );
    const [, snooze] = handlers(renderHomeSuggestions(h));

    await snooze();
    expect(h._homeSuggestions).toEqual([next]);
  });

  it("snoozes a row, and the next one takes its place", async () => {
    const h = host(numbered(4));
    h.hass.callWS = vi.fn(async ({ type }) =>
      type === "selora_ai/home_suggestions" ? numbered(4).slice(1) : {},
    );
    const [, snooze] = handlers(renderHomeSuggestions(h));

    await snooze();
    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/home_suggestions/snooze",
      suggestion_id: "s1",
    });
    expect(titles(renderHomeSuggestions(h))).toEqual([
      "Leak 2",
      "Leak 3",
      "Leak 4",
    ]);
  });

  it("dismisses a row for good", async () => {
    const h = host(numbered(2));
    h.hass.callWS = vi.fn(async ({ type }) =>
      type === "selora_ai/home_suggestions" ? numbered(2).slice(1) : {},
    );
    const [, , dismiss] = handlers(renderHomeSuggestions(h));

    await dismiss();
    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/home_suggestions/dismiss",
      suggestion_id: "s1",
    });
    expect(titles(renderHomeSuggestions(h))).toEqual(["Leak 2"]);
  });

  it("offers to add cameras to Frigate, asking with their ids", () => {
    const cameras = {
      suggestion_id: "frigate:camera.driveway,camera.porch",
      type: "add_to_frigate",
      entities: [
        { entity_id: "camera.porch", name: "Porch" },
        { entity_id: "camera.driveway", name: "Driveway" },
      ],
      collection: { source: "frigate", name: "Frigate", ref: "frigate" },
    };
    const h = host([cameras]);
    expect(flatten(renderHomeSuggestions(h))).toContain(
      "Add 2 cameras to Selora Vision",
    );

    handlers(renderHomeSuggestions(h))[0]();
    const opened = renderHomeSuggestions(h);
    expect(flatten(opened)).toContain(
      "Selora Vision is not watching Porch and Driveway yet.",
    );
    handlers(opened).at(-1)();
    const [sent] = h._askInNewChat.mock.calls[0];
    expect(messageDetails(sent)).toBe(
      "Add these cameras to Selora Vision: Porch, Driveway (camera.porch, camera.driveway).",
    );
    // A Frigate setup guide opens under its banner, named like the row.
    expect(h._askInNewChat.mock.calls[0][1].guide).toEqual({
      icon: "mdi:cctv",
      title: "Add 2 cameras to Selora Vision",
      subtitle: "",
      banner: "frigate",
    });
  });

  it("names a single camera", () => {
    const out = flatten(
      renderHomeSuggestions(
        host([
          {
            suggestion_id: "frigate:camera.porch",
            type: "add_to_frigate",
            entities: [{ entity_id: "camera.porch", name: "Porch" }],
            collection: { source: "frigate", name: "Frigate", ref: "frigate" },
          },
        ]),
      ),
    );
    expect(out).toContain("Add Porch to Selora Vision");
  });

  it("names a camera found on the network by its address", () => {
    const h = host([
      {
        suggestion_id: "frigate:192.168.1.60,camera.porch",
        type: "add_to_frigate",
        entities: [
          { entity_id: "camera.porch", name: "Porch" },
          { entity_id: "", address: "192.168.1.60", name: "Garage Cam" },
        ],
        collection: { source: "frigate", name: "Frigate", ref: "frigate" },
      },
    ]);
    handlers(renderHomeSuggestions(h))[0]();
    expect(flatten(renderHomeSuggestions(h))).toContain(
      "Selora Vision is not watching Porch and Garage Cam (192.168.1.60) yet.",
    );
    handlers(renderHomeSuggestions(h)).at(-1)();

    const [sent] = h._askInNewChat.mock.calls[0];
    expect(messageDetails(sent)).toBe(
      "Add these cameras to Selora Vision: Porch, Garage Cam (camera.porch, 192.168.1.60).",
    );
  });

  it("offers one discovered device, asking without ids", () => {
    const found = (waiting) => ({
      suggestion_id: "discovered:hue:001788",
      type: "set_up_discovered",
      entities: [{ entity_id: "", name: "Hue Bridge" }],
      collection: { source: "discovery", name: "Philips Hue", ref: "" },
      waiting,
    });
    const h = host([found(12)]);
    expect(flatten(renderHomeSuggestions(h))).toContain("Set up Hue Bridge");

    handlers(renderHomeSuggestions(h))[0]();
    const opened = flatten(renderHomeSuggestions(h));
    expect(opened).toContain(
      "Home Assistant found Hue Bridge through Philips Hue, but nobody has set it up yet.",
    );
    expect(opened).toContain(
      "Other devices Home Assistant found, waiting after this one: 12.",
    );
    handlers(renderHomeSuggestions(h)).at(-1)();
    const [sent] = h._askInNewChat.mock.calls[0];
    expect(messageDetails(sent)).toBe(
      "Set up Hue Bridge, which Home Assistant discovered through Philips Hue.",
    );
  });

  it("says nothing of others when it is the last discovered device", () => {
    const h = host([
      {
        suggestion_id: "discovered:hue:001788",
        type: "set_up_discovered",
        entities: [{ entity_id: "", name: "Hue Bridge" }],
        collection: { source: "discovery", name: "Philips Hue", ref: "" },
        waiting: 0,
      },
    ]);
    handlers(renderHomeSuggestions(h))[0]();
    expect(flatten(renderHomeSuggestions(h))).not.toContain("waiting after");
  });

  it("starts face recognition, then asks for the people to recognize", async () => {
    const faces = (state) =>
      host([
        {
          suggestion_id: `faces:${state}`,
          type: "recognize_faces",
          state,
          entities: [],
          collection: { source: "frigate", name: "Frigate", ref: "frigate" },
        },
      ]);
    const off = faces("off");
    expect(flatten(renderHomeSuggestions(off))).toContain(
      "Recognize the people you know on your cameras",
    );
    // Only a server without the guide hands it to the model.
    off.hass.callWS = vi.fn(async () => {
      throw { code: "unknown_command", message: "Unknown command." };
    });
    handlers(renderHomeSuggestions(off))[0]();
    await handlers(renderHomeSuggestions(off)).at(-1)();
    expect(messageDetails(off._askInNewChat.mock.calls[0][0])).toContain(
      "ask me before turning it on",
    );

    expect(flatten(renderHomeSuggestions(faces("empty")))).toContain(
      "Add the people Selora Vision should recognize",
    );
  });

  it("asks the model about the cameras when the server cannot open the chat", async () => {
    const h = host([
      {
        suggestion_id: "describe:driveway,living_room",
        type: "describe_cameras",
        entities: [
          { entity_id: "camera.driveway", name: "Front Driveway" },
          { entity_id: "", name: "Living Room" },
        ],
        collection: { source: "frigate", name: "Frigate", ref: "frigate" },
      },
    ]);
    expect(flatten(renderHomeSuggestions(h))).toContain(
      "Search what your cameras saw, in plain words",
    );

    handlers(renderHomeSuggestions(h))[0]();
    expect(flatten(renderHomeSuggestions(h))).toContain(
      "Pick which cameras to describe: Front Driveway and Living Room.",
    );
    expect(flatten(renderHomeSuggestions(h))).toContain("Choose cameras");
    h.hass.callWS = vi.fn(async () => {
      throw { code: "unknown_command", message: "Unknown command." };
    });
    await handlers(renderHomeSuggestions(h)).at(-1)();
    const [sent] = h._askInNewChat.mock.calls[0];
    expect(messageDetails(sent)).toBe(
      "Ask me, one camera at a time, whether Selora AI should describe what these cameras see: Front Driveway, Living Room.",
    );
  });

  it("says why when the server refuses, and reads the rows again", async () => {
    const h = host([
      {
        suggestion_id: "describe:front_door",
        type: "describe_cameras",
        entities: [{ entity_id: "", name: "Front Door" }],
        collection: { source: "frigate", name: "Frigate", ref: "frigate" },
      },
    ]);
    h._showToast = vi.fn();
    h.hass.callWS = vi.fn(async ({ type }) => {
      if (type === "selora_ai/home_suggestions") return [];
      throw {
        code: "describe_failed",
        message: "Every camera already has an answer.",
      };
    });
    handlers(renderHomeSuggestions(h))[0]();

    await handlers(renderHomeSuggestions(h)).at(-1)();

    // A model chat would ask about cameras that are already answered.
    expect(h._askInNewChat).not.toHaveBeenCalled();
    expect(h._showToast).toHaveBeenCalledWith(
      "Every camera already has an answer.",
      "error",
    );
    expect(h._homeSuggestions).toEqual([]);
  });

  it("opens the face recognition chat the server wrote, with no model turn", async () => {
    const h = host([
      {
        suggestion_id: "faces:off",
        type: "recognize_faces",
        state: "off",
        entities: [],
        collection: { source: "frigate", name: "Frigate", ref: "frigate" },
      },
    ]);
    h.hass.callWS = vi.fn(async () => ({ session_id: "s7" }));
    h._setActiveTab = vi.fn();
    h._openSession = vi.fn(async () => {});
    handlers(renderHomeSuggestions(h))[0]();

    await handlers(renderHomeSuggestions(h)).at(-1)();

    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/frigate/faces_start",
      title: "Recognize the people you know on your cameras",
      language: "en",
    });
    expect(h._openSession).toHaveBeenCalledWith("s7");
    expect(h._askInNewChat).not.toHaveBeenCalled();
  });

  it("opens the describe chat the server wrote, with no model turn", async () => {
    const h = host([
      {
        suggestion_id: "describe:front_door,garage",
        type: "describe_cameras",
        entities: [
          { entity_id: "", name: "Front Door" },
          { entity_id: "", name: "Garage" },
        ],
        collection: { source: "frigate", name: "Frigate", ref: "frigate" },
      },
    ]);
    h.hass.callWS = vi.fn(async () => ({ session_id: "s9" }));
    h._setActiveTab = vi.fn();
    h._openSession = vi.fn(async () => {});
    handlers(renderHomeSuggestions(h))[0]();

    await handlers(renderHomeSuggestions(h)).at(-1)();

    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/frigate/describe_start",
      title: "Help me choose which cameras Selora AI describes",
      language: "en",
    });
    expect(h._openSession).toHaveBeenCalledWith("s9");
    expect(h._askInNewChat).not.toHaveBeenCalled();
  });

  const scene = {
    suggestion_id: "scene:light.ceiling=off,light.reading=on,light.sofa=on",
    type: "create_scene",
    entities: [
      { entity_id: "light.ceiling", name: "Ceiling", state: "off" },
      { entity_id: "light.reading", name: "Reading", state: "on" },
      { entity_id: "light.sofa", name: "Sofa", state: "on" },
    ],
    collection: { source: "usage", name: "", ref: "" },
    days: 4,
  };

  it("proposes a scene for devices set together, with each one's state", () => {
    const h = host([scene]);
    const [toggle] = handlers(renderHomeSuggestions(h));
    toggle();
    const opened = renderHomeSuggestions(h);
    expect(flatten(opened)).toContain(
      "Make a scene for Ceiling, Reading, and Sofa",
    );
    expect(flatten(opened)).toContain("On 4 different days you set");

    handlers(opened).at(-1)();
    const [sent] = h._askInNewChat.mock.calls[0];
    expect(messageDetails(sent)).toContain(
      "Ceiling (light.ceiling): off, Reading (light.reading): on, Sofa (light.sofa): on",
    );
  });

  const porch = {
    suggestion_id: "pattern:abc",
    type: "automation_idea",
    entities: [{ entity_id: "light.porch", name: "Porch" }],
    collection: { source: "pattern", name: "Porch light at dusk", ref: "" },
    automation: {
      alias: "Porch light at dusk",
      description: "Turn the porch light on at 19:00.",
      yaml: "alias: Porch light at dusk\n",
    },
    evidence: "Turned on around 19:00 on 9 of 10 days",
  };

  it("proposes the pattern engine's automation from its YAML", () => {
    const h = host([porch]);
    const [toggle] = handlers(renderHomeSuggestions(h));
    toggle();
    const opened = renderHomeSuggestions(h);
    expect(flatten(opened)).toContain("Porch light at dusk");
    expect(flatten(opened)).toContain(
      "Selora noticed: Turned on around 19:00 on 9 of 10 days.",
    );

    handlers(opened).at(-1)();
    const [sent] = h._askInNewChat.mock.calls[0];
    expect(stripEntityMarkers(sent)).toBe("Porch light at dusk");
    expect(messageDetails(sent)).toContain("alias: Porch light at dusk");
  });
});
