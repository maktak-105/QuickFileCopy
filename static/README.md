# Static UI assets

QuickFileCopy keeps its CSS and JavaScript inline in `templates/index.html` so the
WebView2 UI can be embedded as one resource. This directory is reserved for a future split
into `css/`, `js/`, and `img/`; `bundle_html.py` is the single bundling entry point.
