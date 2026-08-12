# Maia — a film

A two-minute cinematic web film introducing **Maia**, the AI assistant built for
Cat® equipment customers at Mantrac Egypt.

Open `index.html` in any modern browser. There is no build step, no bundler and
no dependency to install — the whole film is a single self-contained file with
no network requests at all.

```sh
open index.html          # macOS
xdg-open index.html      # Linux
```

## Controls

| Input | Action |
| --- | --- |
| `Space` | Pause / resume |
| `←` `→` | Previous / next scene |
| `F` | Full screen |
| `M` | Mute |
| `R` | Replay from the top |
| Swipe | Previous / next scene (touch) |
| Scrubber | Jump to any scene |

## The picture

Fourteen scenes, about 2 minutes 16 seconds, running on a generated score —
a sub-bass bed, a three-voice pad that moves one chord per scene, and air,
bell and blip accents. No audio files; it is all synthesised in the Web Audio
API at runtime, through a compressor so stacked accents cannot clip.

Audio only ever starts on a click, so the film never trips a browser autoplay
policy. **Play without sound** is offered on the title card for anyone who
would rather not be surprised, and sound can still be switched on later.

## Design

One rule governs the whole picture:

> **Yellow is the physical world** — machines, parts, steel, ground.
> **Cyan and violet are the intelligence layer** — vectors, retrieval, the model.

The two palettes stay apart and meet only twice: at the reveal (scene 03) and
at the close (scene 14). When every scene glows every colour, nothing reads.

Type is a deliberate pair. Display and body come from the native system stack,
which resolves to SF Pro on Apple hardware. The second face is `ui-monospace`,
used *only* for part numbers, fault codes, latency figures and vector counts —
because in this domain those genuinely are machine data, and setting them in
the sans throws that signal away.

The film commits to a single dark visual world rather than following the
viewer's light/dark preference. A film is a film. Every colour is painted
explicitly so the page holds on any host background.

## Engineering

**One clock drives everything.** Scene progress, cue firing, the retrieval
canvas and the counters all advance from a single `requestAnimationFrame`
loop. Nothing schedules its own `setTimeout`, which means pause genuinely
pauses — including the conversation, the canvas and the count-up — and
skipping a scene cannot leave a stale timer behind to fire into the next one.

Scene work is registered as cues on that clock (`cue(ms, fn)`) and the whole
cue list is discarded on scene exit.

**Accessibility.** The visual scenes are `aria-hidden`; a full transcript of
all fourteen scenes carries the content for screen readers. Focus states are
visible, the scrubber is keyboard operable, and `prefers-reduced-motion` stops
the movement without breaking the narrative — scenes still advance and still
tell the story.

### Three bugs worth remembering

- **Class-name collision.** The body state class was originally `bloom`, the
  same as the bloom element's class — so `<body>` matched the element rule,
  picked up a `filter`, and became the containing block for every
  `position: fixed` child. The entire film collapsed into one blurred circle.
  The state class is now `bloom-lit`.
- **Background propagation.** The ground must be painted on **both** `html`
  and `body`. Set it on `body` alone and CSS propagates it to the canvas,
  leaving body with no background of its own — the `mix-blend-mode: screen`
  light fields then composite against transparency and blow the frame to white.
- **Animation vs. transform.** The bloom is centred with margins and grown with
  the independent `scale` property, leaving `transform` free for its spin. Put
  the centring in `transform` and the animation's implicit from-keyframe
  overwrites it, so the bloom crawls off-centre across its 24s cycle.

## Content note

Figures in the film are labelled as pilot targets, not measured results:
the 94% faster first response and the 700 ms latency budget are both marked
on screen as targets to be validated in pilot. Keep that framing if you edit
the copy.
