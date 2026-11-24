// Sample temporary-access states for `selora.fileAccess()` (dev-tools.js):
// the header indicator and dialog without an agent asking. Only the panel's
// local state changes — nothing is stored, and a button pressed on a sample
// reaches the backend with an id it does not know, so it only fails.

const MINUTE = 60000;

function iso(offsetMinutes) {
  return new Date(Date.now() + offsetMinutes * MINUTE).toISOString();
}

const AGENT = {
  agent: "An AI assistant connected with your Selora account",
  agent_kind: "selora_account",
  agent_name: null,
  agent_client: "ChatGPT",
  folder: "www/selora",
};

const WRITES = () => [
  {
    file: "www/selora/selora-home.js",
    url: "/local/selora/selora-home.js",
    loaded: true,
    size: 86212,
    at: iso(-18),
    action: "replaced",
    times: 3,
    findings: [
      {
        kind: "service",
        severity: "medium",
        detail: "lock.unlock",
        line: 1067,
      },
      {
        kind: "service",
        severity: "medium",
        detail: "cover (any action)",
        line: 850,
      },
      { kind: "service", severity: "info", detail: "light.turn_on", line: 610 },
      {
        kind: "service",
        severity: "info",
        detail: "climate.set_temperature",
        line: 796,
      },
      {
        kind: "service",
        severity: "info",
        detail: "may also call actions decided while it runs",
        line: 296,
      },
      {
        kind: "external_url",
        severity: "high",
        detail: "fonts.example.com",
        line: 12,
      },
    ],
  },
  {
    file: "www/selora/vendor/lit-core.min.js",
    url: "/local/selora/vendor/lit-core.min.js",
    loaded: false,
    size: 15873,
    at: iso(-20),
    action: "created",
    times: 1,
    findings: [],
  },
];

/** The panel's `_fileAccess` and the dialog to open, for a sample `kind`. */
export function fileAccessSample(kind) {
  if (kind === "request") {
    return {
      state: {
        requests: [
          {
            ...AGENT,
            id: "preview-request",
            reason:
              "Install the Selora Home dashboard card and its Lit library so the new dashboard can load it.",
            minutes: 60,
            created_at: iso(-1),
            expires_at: iso(14),
            status: "pending",
          },
        ],
        grants: [],
      },
      dialog: { kind: "request", id: "preview-request" },
    };
  }
  const ended = kind === "ended";
  return {
    state: {
      requests: [],
      grants: [
        {
          ...AGENT,
          id: "preview-grant",
          reason: "Install the Selora Home dashboard card.",
          approved_by: "You",
          approved_at: iso(ended ? -70 : -18),
          expires_at: iso(ended ? -10 : 42),
          ended_at: ended ? iso(-10) : null,
          ended_reason: ended ? "expired" : null,
          writes: WRITES(),
        },
      ],
    },
    dialog: { kind: "grant", id: "preview-grant" },
  };
}

export const FILE_ACCESS_SAMPLES = ["request", "grant", "ended"];
