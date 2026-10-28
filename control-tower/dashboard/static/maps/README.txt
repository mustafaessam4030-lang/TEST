region.json: the country outlines the Clearance map draws (Africa and the
Middle East, where Mantrac's BUs clear their shipments).

Source: Natural Earth 1:50m Admin 0 countries (public domain,
naturalearthdata.com), as packaged by world-atlas 2.0.2 (ISC licence,
Copyright 2013-2019 Michael Bostock). Built by tools/build_region_map.py;
nothing is fetched at run time.

The map shows only what the eHub export says: how many shipments each BU
has, open and past due. The export has no origin, port or position, so no
routes or moving positions are drawn.

world.json: the world outline the Tracking map draws shipments on (Web
Mercator, 78°N to 56°S). Same Natural Earth 1:50m source, with each
country's ISO 3166 alpha-2 code from GeoNames countryInfo (CC BY 4.0).
Built by tools/build_world_map.py. The Tracking map places a shipment only
where its carrier named a place (dashboard/geo.py, dashboard/places/); it is
never a GPS position.
