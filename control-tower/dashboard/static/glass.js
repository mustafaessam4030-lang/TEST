/* Liquid Glass (iOS 26): the lens that bends the light at a glass edge.
   Adds two SVG filters to the page (#lg-lens for bars, the sidebar and controls;
   #lg-frost, softer, for menus and sheets) and sets html.lg; glass.css uses them
   as a backdrop filter. The lens map is drawn here (no image file): grey in the
   middle (no bend), stronger towards each edge.
   Only Chromium (Chrome, Edge) can bend a backdrop through an SVG filter; any
   other browser, or "reduce transparency", keeps the frosted glass of glass.css.
   Presentation only: nothing is fetched, stored or changed. */
(() => {
  "use strict";
  if (!/Chrome\/|Edg\//.test(navigator.userAgent)) return;
  if (matchMedia("(prefers-reduced-transparency: reduce)").matches) return;
  if (document.getElementById("lg-defs")) return;
  const N = 128;
  const c = document.createElement("canvas");
  c.width = c.height = N;
  const g = c.getContext("2d");
  if (!g) return;
  // the lens map for an edge band (a share of the width and height): none in the middle,
  // rising (eased) to the edge, outward on each side
  const lensMap = (band) => {
    const im = g.createImageData(N, N);
    const bend = (t) => t < band ? Math.pow(1 - t / band, 2) : t > 1 - band ? -Math.pow((t - 1 + band) / band, 2) : 0;
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        const i = (y * N + x) * 4;
        im.data[i] = Math.round(128 + 127 * bend(x / (N - 1)));
        im.data[i + 1] = Math.round(128 + 127 * bend(y / (N - 1)));
        im.data[i + 2] = 128;
        im.data[i + 3] = 255;
      }
    }
    g.putImageData(im, 0, 0);
    return c.toDataURL("image/png");
  };
  const lens = (id, band, soften, scale) => {
    const map = lensMap(band);
    return `<filter id="${id}" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">` +
    `<feImage href="${map}" x="0" y="0" width="100%" height="100%" preserveAspectRatio="none" result="map"/>` +
    `<feGaussianBlur in="SourceGraphic" stdDeviation="${soften}" result="soft"/>` +
    `<feDisplacementMap in="soft" in2="map" scale="${scale}" xChannelSelector="R" yChannelSelector="G"/></filter>`;
  };
  const add = () => {
    document.body.insertAdjacentHTML("beforeend",
      `<svg id="lg-defs" width="0" height="0" style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true" focusable="false">` +
      lens("lg-lens", .2, 4, 44) + lens("lg-frost", .05, 18, 22) + "</svg>");
    document.documentElement.classList.add("lg");
  };
  if (document.body) add(); else addEventListener("DOMContentLoaded", add, {once: true});
})();
