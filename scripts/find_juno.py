import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from astropy.io import fits
from astropy.wcs import WCS


IMAGE = Path("data/raw/test_field_07.png")
WCS_PATH = Path("outputs/test-field-07-catalog/refined_solution.wcs")
OUT = Path("outputs/test-field-07-juno")

OBS_TIME = "2026-09-25_00:50:12"
LAT = 43.783356
LON = -79.187519
ALT_KM = 0.15


def ra_hms_for_api(ra_deg):
    total_hours = (ra_deg % 360.0) / 15.0
    h = int(total_hours)
    m_float = (total_hours - h) * 60
    m = int(m_float)
    s = (m_float - m) * 60
    return f"{h:02d}-{m:02d}-{s:05.2f}"


def dec_dms_for_api(dec_deg):
    prefix = "M" if dec_deg < 0 else ""
    value = abs(dec_deg)
    d = int(value)
    m_float = (value - d) * 60
    m = int(m_float)
    s = (m_float - m) * 60
    return f"{prefix}{d:02d}-{m:02d}-{s:04.1f}"


def parse_ra(ra):
    h, m, s = map(float, ra.split(":"))
    return 15.0 * (h + m / 60.0 + s / 3600.0)


def parse_dec(dec):
    sign = -1 if dec.strip().startswith("-") else 1
    nums = re.findall(r"\d+(?:\.\d+)?", dec)
    d, m, s = map(float, nums[:3])
    return sign * (d + m / 60.0 + s / 3600.0)


OUT.mkdir(parents=True, exist_ok=True)

image = Image.open(IMAGE).convert("RGB")
width, height = image.size

wcs = WCS(fits.getheader(WCS_PATH))

# Derive the query field directly from the solved WCS.
cx = (width - 1) / 2
cy = (height - 1) / 2
ra0, dec0 = wcs.all_pix2world([[cx, cy]], 0)[0]

corners = [
    [0, 0],
    [width - 1, 0],
    [width - 1, height - 1],
    [0, height - 1],
]
world = wcs.all_pix2world(corners, 0)

ra_half = max(
    abs(((ra - ra0 + 180.0) % 360.0) - 180.0)
    for ra, dec in world
)
dec_half = max(abs(dec - dec0) for ra, dec in world)

# Small safety margin around the actual image.
ra_half *= 1.10
dec_half *= 1.10

params = {
    "lat": str(LAT),
    "lon": str(LON),
    "alt": str(ALT_KM),
    "obs-time": OBS_TIME,
    "fov-ra-center": ra_hms_for_api(ra0),
    "fov-dec-center": dec_dms_for_api(dec0),
    "fov-ra-hwidth": f"{ra_half:.6f}",
    "fov-dec-hwidth": f"{dec_half:.6f}",
    "two-pass": "true",
    "suppress-first-pass": "true",
    "req-elem": "false",
    "mag-required": "true",
    "vmag-lim": "18",
    "sb-kind": "a",
}

url = (
    "https://ssd-api.jpl.nasa.gov/sb_ident.api?"
    + urllib.parse.urlencode(params)
)

print("Querying JPL Small-Body Identification API...")
print(f"Field centre: RA={ra0:.6f}°, Dec={dec0:+.6f}°")
print(f"Field half-width: RA={ra_half:.4f}°, Dec={dec_half:.4f}°")

with urllib.request.urlopen(url, timeout=180) as response:
    data = json.load(response)

(OUT / "small_bodies.json").write_text(
    json.dumps(data, indent=2),
    encoding="utf-8",
)

fields = data.get("fields_second", [])
rows = data.get("data_second_pass", [])

print(f"\nSmall bodies returned: {len(rows)}")

objects = []
for row in rows:
    rec = dict(zip(fields, row))
    objects.append(rec)

    name = rec.get("Object name", "")
    mag = rec.get("Visual magnitude (V)", "")
    print(f"  {name:40} V={mag}")

juno = next(
    (r for r in objects if "Juno" in r.get("Object name", "")),
    None,
)

if juno is None:
    print("\n3 Juno was NOT returned inside this solved field.")
    print("See:", OUT / "small_bodies.json")
    raise SystemExit(2)

ra_string = juno["Astrometric RA (hh:mm:ss)"]
dec_string = juno['Astrometric Dec (dd mm\'ss")']

ra_deg = parse_ra(ra_string)
dec_deg = parse_dec(dec_string)

x, y = wcs.all_world2pix([[ra_deg, dec_deg]], 0)[0]

mag = juno.get("Visual magnitude (V)")
inside = 0 <= x < width and 0 <= y < height

result = {
    "object": juno.get("Object name"),
    "observation_time_utc": OBS_TIME.replace("_", "T") + "Z",
    "observer": {
        "latitude_deg": LAT,
        "longitude_deg": LON,
        "altitude_km": ALT_KM,
    },
    "ra_deg": ra_deg,
    "dec_deg": dec_deg,
    "ra_hms": ra_string,
    "dec_dms": dec_string,
    "visual_magnitude": mag,
    "pixel_x": float(x),
    "pixel_y": float(y),
    "inside_image": bool(inside),
    "wcs": str(WCS_PATH),
}

(OUT / "juno_match.json").write_text(
    json.dumps(result, indent=2),
    encoding="utf-8",
)

print("\n3 Juno FOUND")
print(f"RA/Dec: {ra_deg:.7f}°, {dec_deg:+.7f}°")
print(f"Pixel:  ({x:.1f}, {y:.1f})")
print(f"V mag:  {mag}")
print(f"Inside image: {inside}")

if not inside:
    raise SystemExit(3)

# Draw on the untouched original image -- no stretch.
annotated = image.copy()
draw = ImageDraw.Draw(annotated)

try:
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26
    )
except OSError:
    font = ImageFont.load_default()

colour = (255, 0, 255)
r1, r2 = 12, 38
lw = 4

draw.line((x-r2, y, x-r1, y), fill=colour, width=lw)
draw.line((x+r1, y, x+r2, y), fill=colour, width=lw)
draw.line((x, y-r2, x, y-r1), fill=colour, width=lw)
draw.line((x, y+r1, x, y+r2), fill=colour, width=lw)

label = "3 Juno"
if mag not in (None, ""):
    label += f"  V={mag}"

draw.text((x + 45, y - 18), label, fill=colour, font=font)

annotated.save(OUT / "juno_overlay.png")

# Native-resolution contextual crop: do not enlarge/pixelate it.
radius = 250
left = max(0, int(x) - radius)
right = min(width, int(x) + radius)
top = max(0, int(y) - radius)
bottom = min(height, int(y) + radius)

crop = annotated.crop((left, top, right, bottom))
crop.save(OUT / "juno_zoom.png")

print("\nWritten:")
print(" ", OUT / "juno_overlay.png")
print(" ", OUT / "juno_zoom.png")
print(" ", OUT / "juno_match.json")
print(" ", OUT / "small_bodies.json")
