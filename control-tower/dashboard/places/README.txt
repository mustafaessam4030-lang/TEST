places.tsv.gz: the airports, ports, towns and countries the Tracking map can
place a carrier's reported location at (dashboard/geo.py).

Sources, downloaded once and built by tools/build_gazetteer.py; nothing is
fetched at run time:

  OurAirports, airports.csv (public domain), ourairports.com/data
      large and medium airports with an IATA code
  GeoNames, cities15000 and countryInfo (CC BY 4.0), geonames.org
      towns of 15,000 people or more, their Latin-script names, and
      country names and ISO codes. Contains information from GeoNames,
      licensed under the Creative Commons Attribution 4.0 licence.
  Natural Earth 1:10m ports (public domain), naturalearthdata.com
      each port's country from Natural Earth 1:50m countries via
      world-atlas 2.0.2 (ISC)

The map places a shipment only where its carrier's own event names a place
that this table holds, at airport or town precision. It is never a GPS
position. A place the table does not hold, or a name shared by towns of
similar size, is left unplaced.
