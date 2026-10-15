# Third-party notices

`geodata.py` is generated from the following data (simplified and projected with d3-geo; see `tools/build_geodata.mjs`):

- [us-atlas](https://github.com/topojson/us-atlas) — ISC License, Copyright (c) Mike Bostock. Derived from US Census Bureau cartographic boundary files.
- [world-atlas](https://github.com/topojson/world-atlas) — ISC License, Copyright (c) Mike Bostock. Derived from [Natural Earth](https://www.naturalearthdata.com/) (public domain).
- [mledoze/countries](https://github.com/mledoze/countries) — ODbL 1.0. Only used to map ISO 3166-1 alpha-2 codes to numeric codes at build time; its data files are not redistributed here.

Build-time tools (not shipped): [d3-geo](https://github.com/d3/d3-geo), [topojson-client](https://github.com/topojson/topojson-client), [topojson-simplify](https://github.com/topojson/topojson-simplify) — ISC.

At runtime NetCheck queries these public services (no account or key needed). Check their terms before heavy use:

- [ip-api.com](https://ip-api.com/docs/legal) — free tier is for non-commercial use only
- [ipinfo.io](https://ipinfo.io/terms-of-service), [ipwho.is](https://ipwho.is/)
- [Cloudflare speed test](https://speed.cloudflare.com/) — `speed.cloudflare.com`
- [ipip.net](https://www.ipip.net/) — `myip.ipip.net`
