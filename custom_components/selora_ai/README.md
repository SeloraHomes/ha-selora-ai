# selora_ai

The Home Assistant integration itself — the directory HACS installs into a hub's
`custom_components/`. Everything here runs inside Home Assistant's process, so it
can read and change the home directly through HA's registries, state machine and
config entries, and it is held to HA's rules: async throughout, nothing blocking
the event loop, and only the dependencies core's constraints allow.

Each module says what it does in its opening docstring. Design rules and the
reasons behind them are in the repository's [`CLAUDE.md`](../../CLAUDE.md).
