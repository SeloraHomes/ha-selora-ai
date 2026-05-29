import { describe, it, expect } from "vitest";
import { looksTruncatedResponse } from "../chat-actions.js";

describe("looksTruncatedResponse", () => {
  it("does not flag a complete answer ending in a question after bold words", () => {
    const text =
      "The front door is already locked, so there's nothing to unlock.\n\n" +
      "[[entity:lock.front_door|Front Door]]\n\n" +
      "The **Kitchen Door** and **Poorly Installed Door** are currently " +
      "unlocked — want me to lock either of those?";
    expect(looksTruncatedResponse(text, false)).toBe(false);
  });

  it("flags truly unterminated bold (odd ** count)", () => {
    expect(looksTruncatedResponse("Here are your **Lights:", false)).toBe(true);
  });

  it("does not flag balanced bold ending in a period", () => {
    expect(
      looksTruncatedResponse("Turned off the **Kitchen Light**.", false),
    ).toBe(false);
  });

  it("flags a dangling colon", () => {
    expect(looksTruncatedResponse("Your lights are:", false)).toBe(true);
  });

  it("flags a trailing bare article", () => {
    expect(looksTruncatedResponse("I turned off the", false)).toBe(true);
  });

  it("flags a last line that is just a bullet dash", () => {
    expect(looksTruncatedResponse("Devices:\n-", false)).toBe(true);
  });

  it("never flags structurally complete turns", () => {
    expect(looksTruncatedResponse("Your lights are:", true)).toBe(false);
  });

  it("ignores empty and overlong responses", () => {
    expect(looksTruncatedResponse("", false)).toBe(false);
    expect(looksTruncatedResponse("the ".repeat(120), false)).toBe(false);
  });
});
