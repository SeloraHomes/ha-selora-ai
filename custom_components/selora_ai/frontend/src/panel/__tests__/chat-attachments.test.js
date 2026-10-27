import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  _processImageFile,
  addImageAttachments,
  clearUnsupportedNotice,
} from "../chat-attachments.js";

// chat-attachments runs in the browser; vitest uses the node environment here,
// so stub only the three globals the image pipeline touches.
let imageSize = { w: 100, h: 100 };
let canvasCalls;
const origs = {};

beforeEach(() => {
  canvasCalls = { contexts: 0, drawn: 0, encodedAs: [] };
  origs.FileReader = globalThis.FileReader;
  origs.Image = globalThis.Image;
  origs.document = globalThis.document;

  globalThis.FileReader = class {
    readAsDataURL(file) {
      this.result = `data:${file.type};base64,ORIGINALBYTES`;
      this.onload();
    }
  };
  globalThis.Image = class {
    set src(_v) {
      this.naturalWidth = imageSize.w;
      this.naturalHeight = imageSize.h;
      this.onload();
    }
  };
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => {
        canvasCalls.contexts += 1;
        return {
          fillStyle: "",
          fillRect: () => {},
          drawImage: () => {
            canvasCalls.drawn += 1;
          },
        };
      },
      toDataURL: (mime) => {
        canvasCalls.encodedAs.push(mime);
        return `data:${mime};base64,REENCODED`;
      },
    }),
  };
});

afterEach(() => {
  globalThis.FileReader = origs.FileReader;
  globalThis.Image = origs.Image;
  globalThis.document = origs.document;
  imageSize = { w: 100, h: 100 };
  vi.restoreAllMocks();
});

const file = (type, size, name = "x") => ({ type, size, name });

describe("_processImageFile", () => {
  it("passes a small GIF through untouched so animation survives", async () => {
    // A canvas only ever holds the decoded first frame, so re-encoding an
    // animated GIF silently discards every later frame.
    const out = await _processImageFile(file("image/gif", 50 * 1024));
    expect(out.mimeType).toBe("image/gif");
    expect(out.dataUrl).toContain("ORIGINALBYTES");
    expect(canvasCalls.drawn).toBe(0);
    expect(canvasCalls.encodedAs).toEqual([]);
  });

  it("still re-encodes an oversized GIF (animation loss is unavoidable there)", async () => {
    imageSize = { w: 4000, h: 3000 };
    const out = await _processImageFile(file("image/gif", 5 * 1024 * 1024));
    expect(canvasCalls.drawn).toBe(1);
    expect(out.mimeType).toBe("image/jpeg");
  });

  it("re-encodes a small JPEG so EXIF (GPS, camera serial) is stripped", async () => {
    const out = await _processImageFile(file("image/jpeg", 50 * 1024));
    expect(out.dataUrl).not.toContain("ORIGINALBYTES");
    expect(out.dataUrl).toContain("REENCODED");
    expect(out.mimeType).toBe("image/jpeg");
    expect(canvasCalls.drawn).toBe(1);
  });

  it("re-encodes a small PNG losslessly to PNG, keeping screenshot text crisp", async () => {
    const out = await _processImageFile(file("image/png", 50 * 1024));
    expect(out.mimeType).toBe("image/png");
    expect(out.dataUrl).not.toContain("ORIGINALBYTES");
    expect(canvasCalls.encodedAs).toEqual(["image/png"]);
  });

  it("downscales a large PNG to JPEG within the edge cap", async () => {
    imageSize = { w: 4000, h: 2000 };
    const out = await _processImageFile(file("image/png", 4 * 1024 * 1024));
    expect(out.mimeType).toBe("image/jpeg");
    expect(canvasCalls.drawn).toBe(1);
  });
});

describe("a model that can't analyze images", () => {
  const panel = (vision) => ({
    _config: { supports_vision: vision },
    _chatAttachments: [],
    _attachmentNotice: "",
    _t: (_key, fallback) => fallback,
  });

  it("is asked again before an image is refused", async () => {
    const host = panel(false);
    host._loadConfig = vi.fn(async () => {
      host._config = { supports_vision: false };
    });

    await addImageAttachments(host, [file("image/png", 10)]);

    expect(host._loadConfig).toHaveBeenCalledOnce();
    expect(host._attachmentNotice).toBe(
      "Your current AI model can't analyze images.",
    );
  });

  it("takes an image once the fresh config says it can", async () => {
    const host = panel(false);
    host._loadConfig = vi.fn(async () => {
      host._config = { supports_vision: true };
    });

    await addImageAttachments(host, [file("image/png", 10)]);

    expect(host._attachmentNotice).toBe("");
    expect(host._chatAttachments).toHaveLength(1);
  });

  it("drops its notice once the model can, before any send", () => {
    const host = panel(true);
    host._attachmentNotice = "Your current AI model can't analyze images.";
    clearUnsupportedNotice(host);
    expect(host._attachmentNotice).toBe("");

    host._attachmentNotice = "Up to 4 images per message.";
    clearUnsupportedNotice(host);
    expect(host._attachmentNotice).toBe("Up to 4 images per message.");
  });
});
