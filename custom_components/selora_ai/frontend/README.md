# Selora AI panel

The sidebar panel Home Assistant serves for Selora AI. It is a separate frontend
rather than HA's own config UI because chat, proposal cards and confirmations need
a live, streaming conversation that HA's forms cannot host. It talks to the
integration only over websocket, as an authenticated HA client.

The built bundle (`panel.js` + `panel.build.json`) is committed, because HACS
installs this directory as-is and a hub has no build step. Rebuild with
`node build.js` after changing `src/` or any `../translations/*.json` file (the
translations are compiled into the bundle); CI fails when the committed bundle
does not match a fresh build. `npm test` runs the Vitest suites.
