// One ring ripples out of a button when it is clicked. Armed once on the
// panel's shadow root rather than in each button's template: the ring is
// keyed to `data-pressed`, which only a live click sets, so a re-render or a
// reopened chat never replays it. The ring itself is `::after` in
// shared/styles/buttons.css.js.
const PRESSABLE = ".btn, .action-btn";

export function armPressRipple(root) {
  // With reduced motion the ring has no animation, so nothing would ever
  // clear the flag: never set it.
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  root.addEventListener("click", (event) => {
    if (still?.matches) return;
    const button = event.target.closest?.(PRESSABLE);
    if (!button || button.disabled) return;
    // Restart it on a second click before the first ring has faded.
    delete button.dataset.pressed;
    void button.offsetWidth;
    button.dataset.pressed = "";
  });
  // The badge's own pop bubbles here too: only the ring's end clears it.
  const clear = (end) => {
    if (end.pseudoElement !== "::after") return;
    if (end.target.matches?.(PRESSABLE)) delete end.target.dataset.pressed;
  };
  root.addEventListener("animationend", clear);
  root.addEventListener("animationcancel", clear);
}
