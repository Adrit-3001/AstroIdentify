/**
 * AstroIdentify landing page: every visual and motion setting lives here.
 *
 * Change values in this file to tune the page. Rendering, camera motion and page content read
 * these settings but never define their own. Units are given for every value; "range" is a
 * sensible tuning range, not a hard limit.
 *
 * Units used throughout:
 *   s      seconds
 *   u      world units (the star field is about 140 u deep; the camera covers ~100 u in flight)
 *   u/s    world units per second (camera speed)
 *   px     CSS pixels (multiplied by the device pixel ratio internally)
 *   view   fraction of the viewport: x 0 = left edge, 1 = right edge; y 0 = top, 1 = bottom
 *   short  fraction of the viewport's shorter side
 *
 * First things to try:
 *   comet path      cometIntro.path (positions + times), cometIntro.glow (shine)
 *   camera catch-up cometIntro.cameraFollow.delaySeconds / stiffness
 *   slower flight   flight.peakSpeed down (e.g. 40) and/or flight.slowdownSeconds up (e.g. 4.5)
 *   shorter trails  trails.exposureSeconds down (e.g. 0.035) and/or trails.maxLengthPx down
 *   denser sky      stars.visibleCount up (e.g. 14000; mobile scales it by quality.mobile)
 */
export const LANDING_CONFIG = {
  /**
   * Comet intro: plays before the flight and hands over to it while already moving.
   *
   *   0 s ─ wordmark on black ─ comet enters ─ glides + glows ─ pulls away ─┐
   *         camera follows (turn) ─ stars slide sideways ─ camera accelerates ┘─▶ flight
   *
   * All times are seconds since the first frame. Path positions are `view` fractions of the
   * screen *as seen before the camera turns* (x 0 = left edge, y 0 = top edge; values outside
   * 0–1 are off-screen), plus a depth in u, so the same path works on any screen size.
   * enabled: false gives the original opening (flight from the first frame).
   */
  cometIntro: {
    enabled: true,

    /** Centered wordmark shown on black from the first paint. */
    logo: {
      /** When the wordmark starts fading, s. This is the logo hold. Range 1–3. */
      fadeStartSeconds: 1.75,
      /** Fade duration, s. Range 0.3–1.5. */
      fadeSeconds: 0.65,
    },

    /**
     * Comet path keyframes: where the comet is (x, y view fractions; depth u, camera distance)
     * and when (`at`, s). Speed through the curve comes from key spacing: keys close together in
     * space = slow glide, far apart = fast. Mark the key where the comet starts pulling away
     * with `departs: true`; the camera follow delay counts from it. The last two keys should
     * share x/y: that ray is the outbound heading, and the camera turns until that heading
     * becomes the flight's focus of expansion.
     */
    path: [
      { at: 0.55, x: -0.14, y: -0.12, depth: 7 },
      { at: 1.15, x: 0.35, y: 0.3, depth: 6.2 },
      { at: 1.55, x: 0.46, y: 0.365, depth: 5.8 },
      { at: 1.9, x: 0.555, y: 0.38, depth: 6.3, departs: true },
      { at: 2.25, x: 0.72, y: 0.33, depth: 10 },
      { at: 2.65, x: 0.84, y: 0.28, depth: 22 },
      { at: 3.05, x: 0.84, y: 0.28, depth: 48 },
    ],

    /** Nucleus shine near closest approach: a brief local brightening while it keeps moving. */
    glow: {
      /** When the shine peaks, s. Place it inside the slow glide. */
      peakAtSeconds: 1.5,
      /** Width of the shine (full width at half maximum), s. Range 0.2–1. */
      durationSeconds: 0.5,
      /** Extra nucleus brightness at the peak (0 = no shine; 1.5 = 2.5x). Range 0–4. */
      intensity: 1.6,
      /** How much the coma grows at the peak (0.35 = +35% radius). Keep small = local glow. */
      comaGrowth: 0.35,
    },

    /**
     * Small bright nucleus. Comet sizes are px at the closest-approach depth on a screen whose
     * short side is ~900 px; they scale down on smaller screens and shrink with depth.
     */
    nucleus: { radiusPx: 1.7, brightness: 1.6, color: [1.0, 0.97, 0.9] },
    /** Soft halo around the nucleus. */
    coma: { radiusPx: 12, brightness: 0.3, color: [0.62, 0.84, 1.0] },
    /** Tapered tail made of particles laid along the path the comet has just travelled. */
    tail: {
      /** Tail length along the path, as a fraction of the screen's short side at closest approach. */
      lengthShortSide: 0.32,
      /** Particle count. More = smoother tail. Range 60–400. */
      particleCount: 180,
      /** Particle size near the head and at the tail end, px. */
      headSizePx: 2.0,
      endSizePx: 5.5,
      /** Sideways scatter at the tail end, px. */
      spreadPx: 7,
      /** Tail brightness near the head (per particle). Range 0.05–0.4. */
      brightness: 0.15,
      /** How fast the tail fades toward its end: higher = shorter-looking tail. Range 1–3. */
      taper: 1.7,
      /** Particle drift along the tail, tail-lengths per second (gives it life). Range 0–1. */
      flowPerSecond: 0.35,
      color: [0.72, 0.84, 1.0],
      seed: 5,
    },
    /** After the handoff the comet recedes and fades into the stars. */
    fadeOut: { afterHandoffSeconds: 0.15, seconds: 1.0 },

    /** The camera turns to follow the comet's outbound heading like a damped spring. */
    cameraFollow: {
      /** Pause after the comet `departs` before the camera starts turning, s. Range 0.1–0.6. */
      delaySeconds: 0.25,
      /** How quickly the camera catches up, 1/s². Higher = quicker turn. Range 6–40. */
      stiffness: 14,
      /** 1 = settles without overshoot; below 1 overshoots slightly. Range 0.6–1.5. */
      dampingRatio: 1.0,
    },

    /** Stars appear brightest-first as the turn begins, then the full field. */
    stars: {
      /** Start relative to the turn start, s (negative = just before it). */
      emergeOffsetSeconds: -0.1,
      /** Time until the full star field is visible, s. */
      emergeSeconds: 1.3,
      /** Overall star brightness when they first appear, 0–1. */
      initialBrightness: 0.45,
    },
    /** Galaxy and nebula fade in during the turn (relative to the turn start). */
    deepSky: { fadeInOffsetSeconds: 0.2, fadeInSeconds: 1.6 },

    /**
     * Handoff into the flight. The camera is still until leadIn starts, then accelerates from
     * rest; at flightStartsAtSeconds it reaches flight.openingSpeed with matching acceleration
     * and the flight below takes over (its timings are unchanged).
     */
    handoff: {
      /** When the flight's own timeline starts, s. Total intro length. Range 2–4. */
      flightStartsAtSeconds: 2.95,
      /** Forward acceleration from rest before that, s. Range 0.4–1.5. */
      leadInSeconds: 0.8,
      /** Shape of that acceleration: higher = gentler start, stronger finish. Range 1.5–4. */
      leadInExponent: 2.5,
    },
  },

  /**
   * Camera flight: one continuous speed curve.
   *
   *   speed  ▲     ┌──┐ peak
   *          │    /    \
   *          │   /      `-._
   *  opening ├──/           `--.__ ambient
   *          └──┴──┴──────────────┴──▶ time
   *           accel hold  slowdown
   */
  flight: {
    /**
     * Speed when the flight starts, u/s: on the very first frame without the comet intro, or at
     * the handoff with it. Above 0 so the flight never starts from rest. Range 3–20.
     */
    openingSpeed: 12,
    /** Top speed, u/s. Range 25–100. Higher = longer streaks, more distance covered. */
    peakSpeed: 58,
    /**
     * Forward speed after the flight, u/s. The slowdown eases into this, so there is no stop.
     * Range 0–0.3. 0 = only the lateral ambient sway remains.
     */
    ambientSpeed: 0.09,
    /** Time from the first frame to peak speed, s. Range 0.4–2. */
    accelerationSeconds: 0.8,
    /** Time spent at peak speed, s. Range 0–1.5. */
    peakHoldSeconds: 0.4,
    /** Time from peak speed down to the ambient speed, s. Range 1.5–6. */
    slowdownSeconds: 3.0,
    /**
     * Easing names (see EASINGS in timeline.js): 'linear', 'sineInOut', 'cubicInOut',
     * 'quadOut', 'cubicOut', 'longTail'. Every easing here starts or ends with zero slope
     * where it joins the next phase, except 'linear' and the '*Out' easings at their start.
     */
    accelerationEasing: 'sineInOut',
    /**
     * 'longTail' sheds most speed early and then glides for a long time, with zero slope at
     * both ends, so the moment the flight ends is hard to see.
     */
    slowdownEasing: 'longTail',
    /** Only for 'longTail': how front-loaded the braking is. Range 1.5–4. Higher = longer glide. */
    slowdownTailExponent: 2.6,
    /**
     * Brightness of the whole sky on the first frame, 0–1. It rises to 1 over
     * openingBrightnessSeconds. Keep it well above 0 so the first frame is never blank.
     */
    openingBrightness: 0.7,
    /** s. Range 0–0.8. */
    openingBrightnessSeconds: 0.45,
  },

  /** Star trails come from each star's apparent motion over a virtual exposure. */
  trails: {
    /**
     * Virtual shutter time, s. Each trail runs from where the star appeared exposureSeconds ago
     * to where it is now, so trails shorten back into points as the camera slows.
     * Range 0.02–0.1.
     */
    exposureSeconds: 0.055,
    /** Longest trail, px (trails ease into this from 75% of it; never a hard stop). Range 80–400. */
    maxLengthPx: 230,
    /** Also cap trails at this fraction of the viewport's shorter side. Range 0.1–0.4. */
    maxLengthShortSideFraction: 0.26,
    /**
     * How much a long trail dims because its light is spread out, 0–1.
     * 0 = trails as bright as points, 1 = physically conserved (long trails very faint).
     */
    energyConservation: 0.55,
    /** Brightness at a trail's tail relative to its head, 0–1. */
    tailBrightness: 0.1,
    /** Width at a trail's tail relative to its head, 0–1. */
    tailWidth: 0.55,
  },

  /** Field stars: a homogeneous 3-D volume that the camera flies through. */
  stars: {
    /** Seed for the field's random layout. Change it for a different, equally random sky. */
    seed: 20251122,
    /** Approximate stars visible in the settled desktop view. Range 3000–20000. */
    visibleCount: 9000,
    /** Closest depth, u. Stars fade out before reaching it. Range 0.5–3. */
    nearDepth: 1.0,
    /** Farthest depth, u. Stars fade in from it. Range 60–250. */
    farDepth: 140,
    /** Depth over which stars fade near nearDepth / before farDepth, u. */
    nearFadeDepth: 1.8,
    farFadeDepth: 40,
    /** Rendered star core size range, px (radius at 1 sigma, before trails). */
    sizeRangePx: [0.55, 1.7],
    /** Brightest any single star or streak can render (very close stars), 0.5–3. */
    maxApparentBrightness: 1.8,
    /**
     * Intrinsic brightness distribution: brightness = minBrightness * u^(-1/brightnessSlope),
     * u uniform in (0, 1]. Smaller slope = more bright stars. Range 1.2–3.
     */
    brightnessSlope: 1.9,
    minBrightness: 0.16,
    maxBrightness: 2.4,
    /**
     * Apparent brightness falls with depth as (referenceDepth / depth)^depthFalloff.
     * Physical is 2; lower keeps distant stars visible. Range 0.4–1.6.
     */
    depthFalloff: 0.85,
    referenceDepth: 40,
    /** Overall star brightness multiplier. Range 0.5–1.6. */
    brightness: 1.0,
    /** Star colour palette (linear RGB) with relative weights: blue-white, white, yellow, orange. */
    palette: [
      { color: [0.72, 0.82, 1.0], weight: 0.3 },
      { color: [0.95, 0.96, 1.0], weight: 0.38 },
      { color: [1.0, 0.92, 0.78], weight: 0.22 },
      { color: [1.0, 0.76, 0.56], weight: 0.1 },
    ],
  },

  /** A few hand-placed bright foreground stars (positions live in `composition`). */
  foregroundStars: {
    /** Glow halo strength around each foreground star, 0–1. */
    haloStrength: 0.32,
    /** Diffraction-spike length, px, and strength 0–1. Spikes fade out while the star streaks. */
    spikeLengthPx: 26,
    spikeStrength: 0.4,
  },

  /** The main spiral galaxy (procedural; position and size live in `composition`). */
  galaxy: {
    seed: 7,
    /** Depth for drift parallax, u. Beyond the star field, so it drifts less than any star. */
    depth: 150,
    /**
     * How much bigger the galaxy is when settled than on the first frame (approach during the
     * flight). 1 = it never changes size. Range 1–1.8.
     */
    growthDuringFlight: 1.3,
    /** Tilt from face-on, degrees (0 = face-on, 80 = nearly edge-on). */
    inclinationDegrees: 58,
    /** Rotation on screen, degrees clockwise from horizontal. */
    positionAngleDegrees: -24,
    /** Spiral arms and how tightly they wind (pitch angle, degrees; range 10–30). */
    armCount: 2,
    armPitchDegrees: 24,
    /** Overall brightness, 0–2. */
    brightness: 1.0,
    coreColor: [1.0, 0.86, 0.66],
    armColor: [0.6, 0.74, 1.0],
    starFormingColor: [1.0, 0.48, 0.62],
    dustColor: [0.32, 0.22, 0.16],
  },

  /** The diffuse emission nebula behind the galaxy (procedural). */
  nebula: {
    seed: 3.7,
    /** Depth for drift parallax, u. Farther than the galaxy. */
    depth: 320,
    /** Settled size / first-frame size. Range 1–1.4. */
    growthDuringFlight: 1.12,
    /** Overall brightness, 0–1. Keep it faint so it reads as diffuse. */
    brightness: 0.45,
    /** Emission colours (linear RGB): hydrogen-alpha red, oxygen teal, bright cores. */
    hydrogenColor: [0.95, 0.3, 0.5],
    oxygenColor: [0.25, 0.62, 0.8],
    coreColor: [1.0, 0.82, 0.78],
    /** Rotation on screen, degrees. */
    rotationDegrees: 18,
  },

  /** Small, faint background galaxies scattered across the sky. */
  distantGalaxies: {
    seed: 11,
    count: 7,
    /** Diameter range, px. */
    sizeRangePx: [16, 44],
    brightness: 0.45,
    depth: 260,
    growthDuringFlight: 1.15,
  },

  /**
   * Scene composition for the settled sky. Positions are in `view` units, sizes in `short`
   * units. `wide` is used for landscape-ish viewports; `narrow` when the viewport's
   * width / height is below narrowBelowAspectRatio (phones in portrait).
   */
  composition: {
    narrowBelowAspectRatio: 0.9,
    wide: {
      /** Where the flight is heading: stars stream outward from this point. */
      focusOfExpansion: { x: 0.62, y: 0.44 },
      galaxy: { x: 0.72, y: 0.4, diameter: 0.56 },
      nebula: { x: 0.68, y: 0.64, diameter: 1.25 },
      /** depth in u (closer = faster parallax and longer final streak); brightness 0–3. */
      foregroundStars: [
        { x: 0.9, y: 0.16, depth: 9, brightness: 2.4, color: [0.85, 0.9, 1.0] },
        { x: 0.55, y: 0.13, depth: 14, brightness: 1.5, color: [1.0, 0.9, 0.76] },
        { x: 0.94, y: 0.8, depth: 11, brightness: 1.8, color: [1.0, 0.95, 0.9] },
        { x: 0.47, y: 0.86, depth: 16, brightness: 1.2, color: [0.8, 0.86, 1.0] },
      ],
      /** Darker region behind the text (CSS gradient), `view` units. */
      quietZone: { x: 0.27, y: 0.5, radiusX: 0.34, radiusY: 0.42 },
    },
    narrow: {
      focusOfExpansion: { x: 0.56, y: 0.3 },
      galaxy: { x: 0.6, y: 0.27, diameter: 0.92 },
      nebula: { x: 0.42, y: 0.4, diameter: 1.9 },
      foregroundStars: [
        { x: 0.14, y: 0.1, depth: 9, brightness: 2.0, color: [0.85, 0.9, 1.0] },
        { x: 0.9, y: 0.5, depth: 12, brightness: 1.6, color: [1.0, 0.92, 0.8] },
        { x: 0.82, y: 0.93, depth: 15, brightness: 1.2, color: [0.85, 0.88, 1.0] },
      ],
      quietZone: { x: 0.5, y: 0.72, radiusX: 0.75, radiusY: 0.3 },
    },
  },

  /**
   * Ambient drift after the flight: a slow lateral sway, so near stars, galaxy and nebula
   * shift relative to each other by depth. It fades in during the slowdown.
   */
  ambientDrift: {
    /** Sway amplitude, u (x, y). 0 disables. Range 0–1. */
    swayAmplitude: [0.4, 0.22],
    /** Sway period, s (x, y). Longer = slower. Range 30–240. */
    swayPeriodSeconds: [95, 130],
    /**
     * Slowdown progress over which the sway (and pointer parallax) fades in: 0 = slowdown
     * start, 1 = flight end; values above 1 continue past the end. Spanning the end hides
     * exactly when the flight becomes the drift.
     */
    fadeInFromSlowdownProgress: 0.3,
    fadeInToSlowdownProgress: 1.6,
  },

  /** Camera offset that follows the pointer (fine pointers only, after the flight). */
  pointerParallax: {
    enabled: true,
    /** Maximum camera offset, u. Near stars move most. Range 0–0.4. 0 disables. */
    strength: 0.14,
    /** Spring stiffness, 1/s². Higher = follows faster. Range 4–60. */
    stiffness: 14,
    /**
     * Spring damping ratio. 1 = critically damped (no bounce). Below 1 overshoots and bounces;
     * 0.6 is a noticeable bounce. Range 0.4–1.5.
     */
    dampingRatio: 1.0,
  },

  /** Headline / tagline / action reveal, scheduled against the same flight timeline. */
  textReveal: {
    /**
     * When the first element starts appearing, as a fraction of the slowdown (0 = slowdown
     * start, 1 = flight end). Tied to the slowdown so it stays in sync if you change timings.
     */
    startAtSlowdownProgress: 0.5,
    /** Fade-in duration of each element, s. Range 0.6–2.5. */
    durationSeconds: 1.5,
    /** Delay between headline, tagline and action, s. */
    staggerSeconds: 0.28,
    /** Upward settle distance while fading in, px. 0 = fade only. */
    riseDistancePx: 10,
    /** Opacity of the dark region behind the text once settled, 0–1. */
    quietZoneOpacity: 0.62,
  },

  /** Camera lens. */
  camera: {
    /** Field of view across the viewport's shorter side, degrees. Range 40–80. */
    fovShortSideDegrees: 56,
  },

  /** Sky background colour (linear RGB 0–1). */
  background: { color: [0.006, 0.008, 0.02] },

  /** Rendering quality. `mobile` applies when the viewport's shorter side is below maxWidthPx. */
  quality: {
    desktop: { starCountScale: 1.0, maxPixelRatio: 2.0, textureSize: 1024 },
    mobile: { maxWidthPx: 760, starCountScale: 0.45, maxPixelRatio: 1.75, textureSize: 512 },
  },

  /** prefers-reduced-motion: open on the settled sky with the text visible. */
  reducedMotion: {
    /** Keep the very slow ambient sway (true) or show a still sky (false). */
    ambientDrift: false,
  },
};
