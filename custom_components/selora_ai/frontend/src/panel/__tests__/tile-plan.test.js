import { describe, expect, it } from "vitest";
import { planTiles } from "../tile-plan.js";

const ids = (n, p = "light.l") =>
  Array.from({ length: n }, (_, i) => `${p}${i}`);
const tiles = (items) => items.filter((i) => "id" in i).map((i) => i.id);
const headers = (items) =>
  items.filter((i) => !("id" in i)).map((i) => i.areaName);

describe("planTiles", () => {
  it("drops headers when areas hold about one tile each", () => {
    // 14 lights over 12 areas stacked one tile per row under its header.
    const groups = ids(12).map((id, i) => [
      `Area ${i}`,
      i < 2 ? [id, `${id}b`] : [id],
    ]);
    const { now, later } = planTiles(groups, 50);
    expect(headers(now)).toEqual([]);
    expect(tiles(now)).toHaveLength(14);
    expect(later).toEqual([]);
  });

  it("keeps headers when areas group several tiles", () => {
    const groups = [
      ["Kitchen", ids(3, "light.k")],
      ["Office", ids(2, "light.o")],
    ];
    expect(headers(planTiles(groups, 50).now)).toEqual(["Kitchen", "Office"]);
  });

  it("mounts only the first tiles and keeps the rest for later", () => {
    const { now, later, tileCount } = planTiles([[null, ids(200)]], 12);
    expect(tiles(now)).toHaveLength(12);
    expect(tiles(later)).toHaveLength(188);
    expect(tileCount).toBe(200);
  });

  it("collapses many tiles over several areas into one row per area", () => {
    const groups = [
      ["A", ids(6, "light.a")],
      ["B", ids(6, "light.b")],
      ["C", ids(6, "light.c")],
    ];
    const { now, later, sections, tileCount } = planTiles(groups, 12);
    expect(now).toEqual([]);
    expect(later).toEqual([]);
    expect(sections.map((s) => [s.areaName, s.ids.length])).toEqual([
      ["A", 6],
      ["B", 6],
      ["C", 6],
    ]);
    expect(tileCount).toBe(18);
  });

  it("keeps a flat grid when areas hold about one tile each", () => {
    // 16 lights over 9 areas: nine rows of one tile is a single column again.
    const groups = [
      ["Living Room", ids(6, "light.lr")],
      ["Exterior", ids(2, "light.ex")],
      ["Family Room", ids(2, "light.fr")],
      ...ids(6, "light.x").map((id, i) => [`Room ${i}`, [id]]),
    ];
    const { now, later, sections } = planTiles(groups, 12);
    expect(sections).toEqual([]);
    expect(tiles(now)).toHaveLength(12);
    expect(tiles(later)).toHaveLength(4);
  });

  it("renders tiles directly up to the limit", () => {
    const groups = [
      ["A", ids(6, "light.a")],
      ["B", ids(6, "light.b")],
    ];
    const { now, sections } = planTiles(groups, 12);
    expect(sections).toEqual([]);
    expect(tiles(now)).toHaveLength(12);
  });
});
