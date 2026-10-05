# Bundled fonts

The Selora panel uses Inter. It is bundled here so that opening the panel never
calls Google, and the panel keeps its look when the home is offline. The
integration serves this folder at `/api/selora_ai/fonts/` (`fonts.py`), and the
panel adds `fonts.css` to the page.

These fonts are for the panel only. Fonts a dashboard depends on are provisioned
by Selora OS, so a dashboard keeps them when this integration is removed.

Inter is licensed under the SIL Open Font License 1.1, which allows bundling. Its
license is shipped as `OFL.txt` in its folder.

The folder name carries the upstream version. The panel tags `fonts.css` with its
own `?v=`, but the font URL inside it is relative and carries no query string, and
the files are served with long-lived cache headers. A new font version therefore
needs a new folder, otherwise browsers keep serving the old file.

## Source

Taken from the upstream release, never from Google's CSS API.

**Inter 4.1** from https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip,
files `web/InterVariable.woff2` and `LICENSE.txt` (shipped as `OFL.txt`). The
panel uses weights 400 to 800. The variable font covers them all in 352 KB, while
static files for 400 to 700 alone would take 455 KB.

## SHA-256

```
693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3  inter-4.1/InterVariable.woff2
262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a  inter-4.1/OFL.txt
```

Check with `shasum -a 256 -c` from this folder, feeding it the block above.
