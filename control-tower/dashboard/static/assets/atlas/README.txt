ATLAS CHARACTER ASSETS
======================

The dashboard draws ATLAS itself (inline SVG, in index.html: atlasFigure),
so nothing in this folder is required.

To use final, designed renders instead, put them here and list them in
manifest.json. Expected names:

    atlas-idle.webp            Ready            calm, eyes closed
    atlas-monitoring.webp      Monitoring       watching data panels
    atlas-analyzing.webp       Analyzing        focused, panels around it
    atlas-recovering.webp      Recovering       working a workflow / gear
    atlas-success.webp         Verified         relaxed, check mark
    atlas-human-required.webp  Human action     attentive, pointing to the action

and in manifest.json:

    "states": {
      "idle": "atlas-idle.webp",
      "monitoring": "atlas-monitoring.webp",
      "analyzing": "atlas-analyzing.webp",
      "recovering": "atlas-recovering.webp",
      "success": "atlas-success.webp",
      "human_required": "atlas-human-required.webp"
    }

Guidance for the renders:
  * square, transparent background, character centred with ~8% margin;
  * 512 x 512 is plenty (shown at most 150px, so 300px covers 2x screens);
  * WebP, under ~40 KB each;
  * the same character in every state, as in the concept sheet: white shell,
    dark glossy visor, amber eyes, Mantrac-orange accents.

States not listed keep the drawn figure, so renders can arrive one at a time.
