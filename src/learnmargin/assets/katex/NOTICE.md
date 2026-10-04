# KaTeX browser assets

Version: 0.16.22. Source: the official `katex` npm package,
https://registry.npmjs.org/katex/-/katex-0.16.22.tgz.

`katex.min.js`, `katex.min.css`, and WOFF2 fonts are unmodified vendored files.
The renderer embeds the fonts as data URLs in the exported standalone HTML.
KaTeX is MIT licensed; see the adjacent `LICENSE`. Reproduce these files using
`../vendor_katex.py`, which checks the pinned package SHA-512 before copying files.

Documentation: https://katex.org/docs/browser.html and https://katex.org/docs/options.html.
