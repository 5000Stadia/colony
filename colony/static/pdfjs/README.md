PDF.js 6.4.299, legacy browser distribution, Mozilla (Apache-2.0; included LICENSE).

Source: https://github.com/mozilla/pdf.js/releases/download/v6.4.299/pdfjs-6.4.299-legacy-dist.zip
Archive SHA256: c894bce53b2f4c3e13141e088c89cc051a1f7118ba4a2c02ec058798ae1303d1

Selected: display module and matching worker, CMaps, standard fonts, ICC profiles,
and image-decoder WebAssembly plus their licenses. Viewer UI, annotation/editor UI,
sandbox module, QuickJS evaluator, source maps and sample documents are omitted.
No CDN or external service is contacted. Asset hashes are in ../pdfjs-assets.json.

The exact archive digest was checked against the official GitHub release API;
independent read-only review checked paths, licenses, dynamic loading and known
security advisories before adoption. Canvas rendering activates no PDF scripting
or annotation actions. PDFs remain untrusted parser input; this is not a claim
that the parser has no vulnerabilities.

The upstream legacy-build browser support floor is Safari 18 (mostly supported).
