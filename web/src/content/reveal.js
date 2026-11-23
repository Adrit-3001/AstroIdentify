/**
 * Page-content reveal. The HTML text is always in the document (accessible to screen readers
 * and readable without JavaScript); this only sets each element's opacity and a small rise
 * from the shared timeline. Once fully revealed, inline styles are removed so the text sits
 * on a plain, static layer that stays sharp while the sky moves behind it.
 */

/**
 * @param {HTMLElement[]} elements  in reveal order (headline, tagline, action)
 * @param {HTMLElement} quietZone   dark region behind the text
 */
export function createReveal(elements, quietZone, timeline, config) {
  const rise = config.textReveal.riseDistancePx;
  const quietOpacity = config.textReveal.quietZoneOpacity;
  let done = false;

  function update(t) {
    if (done) return;
    let all = true;
    elements.forEach((el, i) => {
      const p = timeline.revealAt(t, i);
      if (p < 1) all = false;
      el.style.opacity = String(p);
      el.style.transform = p < 1 ? `translate3d(0, ${((1 - p) * rise).toFixed(2)}px, 0)` : 'none';
    });
    // The quiet zone deepens with the slowdown so it never shows as a shape during the flight.
    const s = timeline.slowdownProgress(t);
    quietZone.style.opacity = String(quietOpacity * s * s * (3 - 2 * s));
    if (all && s >= 1) finish();
  }

  /** Jump to the final state (reduced motion, keyboard focus during the flight). */
  function finish() {
    done = true;
    for (const el of elements) {
      el.style.opacity = '';
      el.style.transform = '';
    }
    quietZone.style.opacity = String(quietOpacity);
    document.documentElement.classList.remove('is-opening');
  }

  return { update, finish, get done() { return done; } };
}
