import { describe, expect, it, vi } from "vitest";

vi.mock("../chat-attachments.js", () => ({
  _processImageFile: vi.fn(async (file) => ({
    name: file.name,
    mimeType: "image/jpeg",
    dataUrl: "data:image/jpeg;base64,QUJD",
  })),
}));

import {
  addPhotos,
  answerFaces,
  loadPeople,
  peopleState,
} from "../faces-actions.js";
import {
  renderFaceRecognitionReply,
  renderFacesCard,
} from "../render-faces-card.js";

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

const host = (callWS) => ({
  hass: { panels: {}, callWS: vi.fn(callWS) },
  _t: (_key, fallback) => fallback,
  _tn: (_key, count, fallback) => fallback.replace("{count}", count),
  requestUpdate: vi.fn(),
  _messages: [],
});

const INTRO = {
  title: "Face recognition",
  tagline: "Know who is at the door.",
  lead: "Frigate tells you a person is at the door.",
  points: [
    { icon: "mdi:shield-lock-outline", text: "No face leaves the house." },
  ],
};

describe("renderFacesCard", () => {
  it("opens with what it does, then the question", () => {
    const out = flatten(
      renderFacesCard(host(), {
        faces_card: { intro: INTRO, question: "Turn on face recognition?" },
      }),
    );
    expect(out).toContain("Know who is at the door.");
    expect(out).toContain("No face leaves the house.");
    expect(out).toContain("Turn on face recognition?");
  });

  it("says a no in its own words, with no people card", () => {
    const out = flatten(
      renderFacesCard(host(), {
        faces_card: { done: "declined", status: "OK, it stays off." },
      }),
    );
    expect(out).toContain("OK, it stays off.");
    expect(out).not.toContain("Add photos");
  });

  it("waits for Frigate before it takes a photo", async () => {
    const h = host(async () => ({ running: false, people: [] }));
    const msg = {
      faces_card: {
        people: true,
        title: "Face recognition",
        done: "restarting",
      },
    };
    flatten(renderFacesCard(h, msg));
    await vi.waitFor(() => expect(peopleState(h).loaded).toBe(true));

    const out = flatten(renderFacesCard(h, msg));

    expect(out).toContain("Waiting for Frigate to restart");
    expect(out).not.toContain("Add photos");
    clearTimeout(peopleState(h).timer);
  });

  it("lists whom Frigate knows and offers to add someone", async () => {
    const h = host(async () => ({
      running: true,
      people: [{ name: "Alice", photos: 3 }],
    }));
    await loadPeople(h);

    const out = flatten(
      renderFacesCard(h, { faces_card: { people: true, title: "Faces" } }),
    );

    expect(out).toContain("Alice");
    expect(out).toContain("3 photos");
    expect(out).toContain("Add photos");
  });
});

describe("renderFaceRecognitionReply", () => {
  it("draws the people card under a reply that read it on", () => {
    const h = host(async () => ({ running: true, people: [] }));
    const msg = {
      tool_calls: [
        { tool: "get_face_recognition", result: { face_library: true } },
      ],
    };
    expect(flatten(renderFaceRecognitionReply(h, msg))).toContain(
      "People Frigate recognizes",
    );
  });

  it("draws nothing while it is off", () => {
    const msg = {
      tool_calls: [
        { tool: "get_face_recognition", result: { face_recognition: "off" } },
      ],
    };
    expect(renderFaceRecognitionReply(host(), msg)).toBe("");
  });
});

describe("faces actions", () => {
  it("sends the answer to the server, not the model", async () => {
    const said = { role: "user", content: "Turn on face recognition" };
    const h = host(async () => ({ messages: [said] }));
    h._activeSessionId = "s1";
    const action = { value: "Turn on", face_answer: { enable: true } };
    const msg = { quick_actions: [action] };

    await answerFaces(h, msg, action);

    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/frigate/faces_answer",
      session_id: "s1",
      enable: true,
    });
    expect(h._messages).toEqual([said]);
    expect(msg._qa_used).toBe(true);
  });

  it("sends each photo on its own and reports the ones without a face", async () => {
    const h = host(async (msg) => {
      if (msg.type === "selora_ai/frigate/faces_people") {
        return { running: true, people: [{ name: "Alice", photos: 1 }] };
      }
      if (msg.name && msg.image && h.hass.callWS.mock.calls.length === 1) {
        throw new Error("Frigate refused it: No face was detected.");
      }
      return { name: "Alice" };
    });
    peopleState(h).name = " Alice ";
    const files = [
      new File(["a"], "back.jpg", { type: "image/jpeg" }),
      new File(["b"], "front.jpg", { type: "image/jpeg" }),
      new File(["c"], "clip.gif", { type: "image/gif" }),
    ];

    await addPhotos(h, files);

    const sent = h.hass.callWS.mock.calls
      .map(([m]) => m)
      .filter((m) => m.type === "selora_ai/frigate/faces_add_photo");
    expect(sent).toHaveLength(2);
    expect(sent[0]).toEqual({
      type: "selora_ai/frigate/faces_add_photo",
      name: "Alice",
      content_type: "image/jpeg",
      image: "QUJD",
    });
    const result = peopleState(h).result;
    expect(result.added).toBe(1);
    expect(result.failed.map((f) => f.file)).toEqual(["back.jpg", "clip.gif"]);
    expect(peopleState(h).people).toEqual([{ name: "Alice", photos: 1 }]);
    expect(peopleState(h).name).toBe(" Alice ");
  });

  it("empties the name for the next person once every photo is in", async () => {
    const h = host(async (msg) =>
      msg.type === "selora_ai/frigate/faces_people"
        ? { running: true, people: [{ name: "Alice", photos: 2 }] }
        : { name: "Alice" },
    );
    peopleState(h).name = "Alice";

    await addPhotos(h, [new File(["a"], "a.jpg", { type: "image/jpeg" })]);

    expect(peopleState(h).name).toBe("");
    expect(peopleState(h).result).toMatchObject({ name: "Alice", added: 1 });
  });
});
