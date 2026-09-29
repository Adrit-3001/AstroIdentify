"""Allow ``python -m astroidentify ...`` as an alternative to the console script."""

from astroidentify.cli import main

raise SystemExit(main())
