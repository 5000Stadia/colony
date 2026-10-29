"""Chat media uses the opener's existing project-file boundary and bundled browser assets."""
import html
import hashlib
from functools import lru_cache
from importlib.resources import files
import json
import mimetypes

VERSION = '6.4.299'
STATIC = files('colony').joinpath('static')


@lru_cache(maxsize=1)
def version():
    return hashlib.sha256(STATIC.joinpath('media.js').read_bytes() + STATIC.joinpath('media.css').read_bytes()).hexdigest()[:12]


def asset(name):
    """Only shipped assets, never a user-supplied filesystem path (also works in colony.pyz)."""
    allowed = {'media.js', 'media.css'} | set(json.loads(STATIC.joinpath('pdfjs-assets.json').read_text()))
    if name not in allowed:
        return None
    kind = {'js': 'text/javascript', 'mjs': 'text/javascript', 'css': 'text/css', 'wasm': 'application/wasm'}
    return STATIC.joinpath(name).read_bytes(), kind.get(name.rsplit('.', 1)[-1], mimetypes.guess_type(name)[0] or 'application/octet-stream')


def expand(label):
    return (f"<button type='button' class='media-expand' aria-label='Expand {html.escape(label, quote=True)}' title='Expand'>"
            "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' stroke='currentColor' stroke-width='1.8' aria-hidden='true'>"
            "<path d='M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5'/></svg></button>")


def picture(url, alt):
    u, label = html.escape(url, quote=True), html.escape(alt, quote=True)
    return (f"<span class='chatmedia' data-media='image' data-src='{u}' data-label='{label or 'Picture'}'>"
            f"<a class='shot' href='{u}' target='_blank' rel='noopener'><img src='{u}' alt='{label}' loading='lazy'></a>"
            + expand('picture') + '</span>')


def pdf(url, label):
    u, title = html.escape(url, quote=True), html.escape(label, quote=True)
    return (f"<span class='chatmedia' data-media='pdf' data-src='{u}' data-label='{title}'>"
            "<span class='media-pdf-preview' role='img' aria-label='PDF preview'>Loading PDF preview…</span>"
            f"<a class='file' href='{u}' target='_blank' rel='noopener'>📄 {title}</a>" + expand('PDF') + '</span>')


def table(markup):
    return "<div class='chatmedia' data-media='table' data-label='Chart'>" + expand('chart') + markup + '</div>'
