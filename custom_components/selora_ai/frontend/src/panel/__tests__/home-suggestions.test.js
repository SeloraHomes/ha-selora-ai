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
  if (typeof node === "function") return "";
  return String(node);
}

// Every event handler in a template, in document order: a row's toggle and
// dismiss, then the open row's "Ask Selora to do it".
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
      "Add 2 cameras to Frigate",
    );

    handlers(renderHomeSuggestions(h))[0]();
    const opened = renderHomeSuggestions(h);
    expect(flatten(opened)).toContain(
      "Frigate is not watching Porch and Driveway yet.",
    );
    handlers(opened).at(-1)();
    const [sent] = h._askInNewChat.mock.calls[0];
    expect(messageDetails(sent)).toBe(
      "Add these cameras to Frigate: Porch, Driveway (camera.porch, camera.driveway).",
    );
    // A Frigate setup guide opens under its banner, named like the row.
    expect(h._askInNewChat.mock.calls[0][1].guide).toEqual({
      icon: "mdi:cctv",
      title: "Add 2 cameras to Frigate",
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
    expect(out).toContain("Add Porch to Frigate");
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
      "Frigate is not watching Porch and Garage Cam (192.168.1.60) yet.",
    );
    handlers(renderHomeSuggestions(h)).at(-1)();

    const [sent] = h._askInNewChat.mock.calls[0];
    expect(messageDetails(sent)).toBe(
      "Add these cameras to Frigate: Porch, Garage Cam (camera.porch, 192.168.1.60).",
    );
  });

  it("starts face recognition, then asks for the people to recognize", () => {
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
    handlers(renderHomeSuggestions(off))[0]();
    handlers(renderHomeSuggestions(off)).at(-1)();
    expect(messageDetails(off._askInNewChat.mock.calls[0][0])).toContain(
      "ask me before turning it on",
    );

    expect(flatten(renderHomeSuggestions(faces("empty")))).toContain(
      "Add the people Frigate should recognize",
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
});
