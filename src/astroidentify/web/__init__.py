"""Thin web interface around the AstroIdentify pipeline (``astroidentify serve``).

Layers, kept separate:

* ``validation`` - checks an upload (name, size, file signature) before any analysis;
* ``jobs``       - one directory per upload, job state on disk, the queue and retention;
* ``worker``     - runs the ordinary pipeline steps for one job in its own process;
* ``annotations``- turns the steps' outputs into markers and the annotated image;
* ``server``     - the HTTP API and static files.

The analysis itself is the CLI pipeline, unchanged: the worker runs the same
``detect -> solve -> catalog-match -> identify -> assess`` commands as ``astroidentify run``.
"""
