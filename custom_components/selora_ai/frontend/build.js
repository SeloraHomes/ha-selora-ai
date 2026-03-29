const { readFileSync } = require("fs");
const { execSync } = require("child_process");
const esbuild = require("esbuild");

// Format source files first
execSync("npx prettier --write 'src/**/*.js' build.js postbuild.js", {
  stdio: "inherit",
});

const manifest = JSON.parse(readFileSync("../manifest.json", "utf-8"));
const version = JSON.stringify(manifest.version);

const define = { __SELORA_VERSION__: version };

async function build() {
  await esbuild.build({
    entryPoints: ["src/panel.js"],
    bundle: true,
    format: "esm",
    outfile: "panel.js",
    define,
  });
  await esbuild.build({
    entryPoints: ["src/card.js"],
    bundle: true,
    format: "esm",
    outfile: "card.js",
    define,
  });
  require("./postbuild.js");

  // Format built output
  execSync("npx prettier --write panel.js card.js", { stdio: "inherit" });
}

build();
