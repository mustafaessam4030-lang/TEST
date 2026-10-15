# Branding

Everything that makes the product look like **Luma** lives in this folder:

| File | What it controls |
|---|---|
| `brand.js` | Product name, tagline, organisation name, logo paths, browser theme colours |
| `brand.css` | The colour palette for the light and dark themes (navy / indigo) |
| `../luma/luma-mascot.svg` | Luma, the mascot: original vector artwork drawn from the approved concept (`docs/luma-mascot/`) |
| `../luma/luma-icon.svg` | The app icon: Luma's head on a navy tile. Sidebar mark, browser tab and installed-app icon. The PNGs beside it (180, 192, 512 px), `../favicon.ico` and `../manifest.webmanifest` are made from it |
| `org-logo.png` | The Mantrac logo (Mantrac and CAT dealer lockup), supplied by the project owner at Mantrac. It is used exactly as supplied; only the empty white margin was trimmed. Shown at the foot of the sidebar. To replace it, put the new authorized file here (or `org-logo.svg`, which takes priority). If neither file exists, nothing is shown. |

No organisation logo is drawn, traced or approximated anywhere in the code. Use only an official
asset supplied by the brand owner, unmodified: never recolour, stretch, crop into or round its corners.
