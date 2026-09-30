# KaTeX browser assets

This directory contains the minified browser distribution and fonts from
KaTeX 0.18.10. They are served locally so problem statements do not depend on
a third-party CDN at runtime.

Source: <https://www.npmjs.com/package/katex/v/0.18.10>

The upstream license is preserved in `LICENSE`. To update these files, copy
`dist/katex.min.js`, `dist/katex.min.css`, and all files under
`dist/fonts/` from the new npm release, then update this note and rerun the
static-resource and Markdown rendering tests.
