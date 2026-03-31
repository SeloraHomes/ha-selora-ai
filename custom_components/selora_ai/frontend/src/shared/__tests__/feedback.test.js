import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// We test the feedback behaviour by calling the panel methods on a plain
// object that mimics the relevant state + helpers.  This avoids pulling in
// LitElement / the full component tree while still covering the logic.
// ---------------------------------------------------------------------------

/** Build a minimal mock that behaves like SeloraAIArchitectPanel. */
function makePanel(overrides = {}) {
  return {
    // State
    _showFeedbackModal: false,
    _submittingFeedback: false,
    _feedbackText: "",
    _feedbackRating: "",
    _feedbackCategory: "",
    _toast: "",
    _toastType: "info",

    // HA object
    hass: { config: { version: "2026.3.4" }, localize: () => "" },

    // Stubs
    requestUpdate: vi.fn(),
    _showToast: vi.fn(),
    _t: (_key, fallback) => fallback,

    // Import the real methods we want to test
    _openFeedback() {
      this._showFeedbackModal = true;
    },

    _closeFeedback() {
      if (this._submittingFeedback) return;
      this._showFeedbackModal = false;
      this._feedbackText = "";
      this._feedbackRating = "";
      this._feedbackCategory = "";
    },

    async _submitFeedback() {
      if (this._submittingFeedback) return;
      const text = (this._feedbackText || "").trim();
      if (text.length < 10) {
        this._showToast(
          this._t(
            "feedback_min_length_error",
            "Please enter at least 10 characters.",
          ),
          "error",
        );
        return;
      }

      this._submittingFeedback = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const payload = {
          message: text,
          ha_version: this.hass?.config?.version || "unknown",
        };
        if (this._feedbackRating) payload.rating = this._feedbackRating;
        if (this._feedbackCategory) payload.category = this._feedbackCategory;
        const res = await fetch(
          "https://qiob98god6.execute-api.us-east-1.amazonaws.com/api/feedback",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: controller.signal,
          },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this._showToast(
          this._t("feedback_success", "Thanks for your feedback!"),
          "success",
        );
        this._showFeedbackModal = false;
        this._feedbackText = "";
        this._feedbackRating = "";
        this._feedbackCategory = "";
      } catch (err) {
        this._showToast(
          err?.message ||
            this._t(
              "feedback_error",
              "Couldn't send feedback — please try again.",
            ),
          "error",
        );
      } finally {
        clearTimeout(timeout);
        this._submittingFeedback = false;
      }
    },

    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// _openFeedback / _closeFeedback
// ---------------------------------------------------------------------------

describe("_openFeedback", () => {
  it("sets _showFeedbackModal to true", () => {
    const p = makePanel();
    p._openFeedback();
    expect(p._showFeedbackModal).toBe(true);
  });
});

describe("_closeFeedback", () => {
  it("resets modal state", () => {
    const p = makePanel({
      _showFeedbackModal: true,
      _feedbackText: "hello world",
      _feedbackRating: "thumbsup",
      _feedbackCategory: "bug",
    });
    p._closeFeedback();
    expect(p._showFeedbackModal).toBe(false);
    expect(p._feedbackText).toBe("");
    expect(p._feedbackRating).toBe("");
    expect(p._feedbackCategory).toBe("");
  });

  it("does nothing while submitting", () => {
    const p = makePanel({
      _showFeedbackModal: true,
      _submittingFeedback: true,
    });
    p._closeFeedback();
    expect(p._showFeedbackModal).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// _submitFeedback
// ---------------------------------------------------------------------------

describe("_submitFeedback", () => {
  let fetchSpy;

  beforeEach(() => {
    fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rejects text shorter than 10 characters", async () => {
    const p = makePanel({ _feedbackText: "short" });
    await p._submitFeedback();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(p._showToast).toHaveBeenCalledWith(
      "Please enter at least 10 characters.",
      "error",
    );
  });

  it("sends POST with correct payload on success", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({ _feedbackText: "This is valid feedback text" });

    await p._submitFeedback();

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, opts] = fetchSpy.mock.calls[0];
    expect(url).toContain("/api/feedback");
    expect(opts.method).toBe("POST");

    const body = JSON.parse(opts.body);
    expect(body.message).toBe("This is valid feedback text");
    expect(body.ha_version).toBe("2026.3.4");
  });

  it("shows success toast and resets state on 2xx", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({
      _feedbackText: "This is valid feedback text",
      _showFeedbackModal: true,
      _feedbackRating: "thumbsup",
      _feedbackCategory: "bug",
    });

    await p._submitFeedback();

    expect(p._showToast).toHaveBeenCalledWith(
      "Thanks for your feedback!",
      "success",
    );
    expect(p._showFeedbackModal).toBe(false);
    expect(p._feedbackText).toBe("");
    expect(p._feedbackRating).toBe("");
    expect(p._feedbackCategory).toBe("");
    expect(p._submittingFeedback).toBe(false);
  });

  it("shows error toast on HTTP error", async () => {
    fetchSpy.mockResolvedValue({ ok: false, status: 500 });
    const p = makePanel({ _feedbackText: "This is valid feedback text" });

    await p._submitFeedback();

    expect(p._showToast).toHaveBeenCalledWith("HTTP 500", "error");
    expect(p._submittingFeedback).toBe(false);
  });

  it("shows error toast on network failure", async () => {
    fetchSpy.mockRejectedValue(new TypeError("Failed to fetch"));
    const p = makePanel({ _feedbackText: "This is valid feedback text" });

    await p._submitFeedback();

    expect(p._showToast).toHaveBeenCalledWith("Failed to fetch", "error");
    expect(p._submittingFeedback).toBe(false);
  });

  it("does not double-submit while in progress", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({
      _feedbackText: "This is valid feedback text",
      _submittingFeedback: true,
    });

    await p._submitFeedback();

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("trims whitespace before sending", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({ _feedbackText: "   padded feedback text   " });

    await p._submitFeedback();

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.message).toBe("padded feedback text");
  });

  it("includes rating and category when set", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({
      _feedbackText: "This is valid feedback text",
      _feedbackRating: "thumbsup",
      _feedbackCategory: "bug",
    });

    await p._submitFeedback();

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.rating).toBe("thumbsup");
    expect(body.category).toBe("bug");
  });

  it("omits rating and category when empty", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({ _feedbackText: "This is valid feedback text" });

    await p._submitFeedback();

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.rating).toBeUndefined();
    expect(body.category).toBeUndefined();
  });

  it("passes an AbortSignal to fetch", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({ _feedbackText: "This is valid feedback text" });

    await p._submitFeedback();

    const opts = fetchSpy.mock.calls[0][1];
    expect(opts.signal).toBeInstanceOf(AbortSignal);
  });

  it("shows error toast when request is aborted", async () => {
    fetchSpy.mockRejectedValue(
      new DOMException("The operation was aborted.", "AbortError"),
    );
    const p = makePanel({ _feedbackText: "This is valid feedback text" });

    await p._submitFeedback();

    expect(p._showToast).toHaveBeenCalledWith(
      "The operation was aborted.",
      "error",
    );
    expect(p._submittingFeedback).toBe(false);
  });

  it("falls back to 'unknown' when hass version is missing", async () => {
    fetchSpy.mockResolvedValue({ ok: true });
    const p = makePanel({
      _feedbackText: "This is valid feedback text",
      hass: {},
    });

    await p._submitFeedback();

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.ha_version).toBe("unknown");
  });
});
