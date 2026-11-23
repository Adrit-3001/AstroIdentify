/** Small deterministic PRNG (mulberry32), so the same seed always gives the same sky. */
export function createRandom(seed) {
  let a = Math.floor(seed * 2654435761) >>> 0 || 0x9e3779b9;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    range: (lo, hi) => lo + (hi - lo) * next(),
    /** Pick an item from [{weight}, ...] by weight. */
    weighted(items) {
      const total = items.reduce((s, it) => s + it.weight, 0);
      let r = next() * total;
      for (const it of items) {
        r -= it.weight;
        if (r <= 0) return it;
      }
      return items[items.length - 1];
    },
  };
}
