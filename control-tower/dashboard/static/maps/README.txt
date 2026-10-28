world.json: the world outline the Tracking map draws shipments on (Web
Mercator, 78°N to 56°S). Same Natural Earth 1:50m source, with each
country's ISO 3166 alpha-2 code from GeoNames countryInfo (CC BY 4.0).
Built by tools/build_world_map.py. The Tracking map places a shipment only
where its carrier named a place (dashboard/geo.py, dashboard/places/); it is
never a GPS position.
