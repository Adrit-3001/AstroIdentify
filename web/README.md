# AstroIdentify landing page

A static page: plain HTML/CSS and JavaScript ES modules rendering the sky with WebGL2.
No build step and no dependencies.

## Run

ES modules don't load from `file://`, so serve the folder:

```bash
python3 -m http.server 8765 -d web
```

Then open <http://127.0.0.1:8765/>. Reload to replay the opening.

Debug URL parameters (for tuning):

| Parameter | Effect |
| --- | --- |
| `?t=1.0` | freeze the clock at 1.0 s since the page opened (comet intro included) |
| `?nointro` | skip the comet intro for this load (the original flight-first opening) |
| `?reduced` | behave as if `prefers-reduced-motion` were set |
| `?hud` | show time, speed and phase in the corner |

Motion tests run in the browser at <http://127.0.0.1:8765/tests/> (the page title becomes
`PASS` or `FAIL`). They check:

- **The flight:** speed-curve continuity, frame-rate independence (60 Hz, 120 Hz and jittery
  144 Hz), trail lengths on the first frame, at peak and when settled, the final-second
  deceleration, and the text reveal schedule.
- **The comet intro:**
  - the camera stays still while the comet enters;
  - the comet slows and shines while moving, then accelerates before the camera turns;
  - the launch into the flight is one continuous acceleration;
  - star motion goes from a sideways sweep to radial flow without a cut;
  - trail lengths have no jumps;
  - the wordmark is gone before the heading;
  - the comet ends at the vanishing point.

## Tuning

Every visual and motion setting is in [`src/config.js`](src/config.js), grouped by purpose
with units and ranges. Start with these:

| Goal | Change |
| --- | --- |
| Move the comet's flyby | `cometIntro.path`: keyframes `{ at, x, y, depth }` in screen fractions |
| Comet shine | `cometIntro.glow.intensity`, `.peakAtSeconds`, `.durationSeconds` |
| How fast the camera catches up | `cometIntro.cameraFollow.delaySeconds`, `.stiffness`, `.dampingRatio` |
| Shorter or longer intro | `cometIntro.handoff.flightStartsAtSeconds`, `cometIntro.logo.fadeStartSeconds` |
| Turn the intro off | `cometIntro.enabled: false` |
| Slower flight | `flight.peakSpeed` down (e.g. 40), `flight.slowdownSeconds` up (e.g. 4.5) |
| Shorter trails | `trails.exposureSeconds` down (e.g. 0.035), `trails.maxLengthPx` down |
| Denser sky | `stars.visibleCount` up (e.g. 14000); phones use `quality.mobile.starCountScale` |

One clock drives everything:
- **The comet intro** (`src/intro/introTimeline.js`) runs on seconds since the first frame.
- **The flight** (`src/timeline.js`) runs on that time minus
  `cometIntro.handoff.flightStartsAtSeconds`.

So the comet, camera turn, wordmark, trails and text reveal stay in sync. The flight settings
work the same with the intro on or off. With the intro on, the camera accelerates from rest
and reaches `flight.openingSpeed` exactly at the handoff, with matching acceleration, so there
is no second launch.

Changing `flight.slowdownSeconds` also moves the text reveal, because the reveal is set as a
fraction of the slowdown (`textReveal.startAtSlowdownProgress`).

**Comet path coordinates.**
- `x`/`y` are fractions of the screen as seen before the camera turns: 0 is the left or top
  edge, 1 the right or bottom edge, and values outside 0–1 are off-screen. `depth` is the
  comet's distance in world units.
- Keys close together in space make a slow glide; keys far apart make it fast.
- The last two keys share `x`/`y`. That ray is the comet's outbound heading. The camera's
  starting direction is derived so that turning to follow that ray lines it up with the
  flight's vanishing point.

## Structure

```text
web/
  index.html            page content (accessible HTML above the canvas)
  styles.css            page layout and typography
  src/config.js         all visual and motion settings
  src/timeline.js       flight: speed curve -> distance, phases, reveal schedule (pure functions of t)
  src/intro/            comet intro: its timeline (logo, glow, camera follow, star emergence)
                        and its view geometry (comet spline, starting camera orientation)
  src/camera.js         camera offsets and turn for a moment and one exposure earlier; pointer spring
  src/math3.js          small 3-D rotation helpers
  src/scene/            WebGL: composition/lens, star trails, galaxy and nebula, comet, renderer
  src/content/reveal.js applies the timeline to the headline, tagline and action
  src/main.js           clock, wiring, reduced motion, resize, debug parameters
  tests/                browser-run motion tests
```

How the motion works:
- **Star field.** The stars are a homogeneous 3-D volume that wraps around the camera, so
  the first frame, the peak and the settled view are one continuous sky.
- **Trails.** Each trail is the segment between a star's projected position one exposure
  ago and now. Trail length therefore follows the star's real apparent motion: close stars
  streak, and distant stars near the heading stay points. Trails are capped in length.
- **Galaxy and nebula.** They are generated once on the GPU and approach by a set growth
  factor. Their approach eases to an exact stop, while the field stars ease into a very
  slow forward drift. A lateral sway fades in across the flight's end.

Without WebGL2 the page still shows all text on a dark background.
