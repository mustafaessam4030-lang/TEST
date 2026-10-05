ATLAS INTRO FILM
================

The 30-second ATLAS product film from the design handoff
(design_handoff_atlas_film), played as the dashboard's introduction and at
/intro.

  film.js         the film + its player, one bundled file (Preact included;
                  MIT licence header at the top). Nothing is fetched from the
                  network.
  film.src.jsx    the source it was built from: the handoff's atlas-film-v2.jsx
                  (Piece) unchanged apart from the asset path, plus our player.
  atlas/          the approved ATLAS pose renders and the MANTRAC logo, as
                  supplied in the handoff.

Rebuild after editing film.src.jsx:

  npm install esbuild preact@10
  npx esbuild film.src.jsx --bundle --minify --format=iife \
      --jsx-factory=__h --jsx-fragment=__Frag --target=es2018 --outfile=film.js

(then put the licence header back on line 1.)

The film shows the security step as "Operator completes this step" — no
CAPTCHA or code is ever drawn.
