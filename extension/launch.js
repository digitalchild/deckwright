// Claude Desktop extension launcher: runs the Deckwright MCP server in Docker over stdio.
//
// Claude Desktop starts this script with its own Node.js. It finds Docker, pulls the image on first use,
// then runs the container with the person's Deckwright folder mounted at /data. stdout carries the MCP
// protocol, so every message from this script goes to stderr.
//
// No dependencies: Node.js built-ins only.

"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

// The release build replaces this with the image pinned by digest.
const IMAGE = process.env.DECKWRIGHT_IMAGE || "__IMAGE__";
const FOLDERS = ["Decks", "Templates", "Inbox"];

function say(message) {
  process.stderr.write(`deckwright: ${message}\n`);
}

function fail(message) {
  say(message);
  process.exit(1);
}

function folder() {
  let raw = (process.env.DECKWRIGHT_FOLDER || "").trim();
  // Claude Desktop passes "${user_config.folder}" as is when the setting is empty, and does not expand
  // ${HOME} in a value.
  raw = raw.replace(/\$\{HOME\}/g, os.homedir());
  if (!raw || raw.includes("${")) raw = path.join(os.homedir(), "Deckwright");
  const expanded = raw === "~" || raw.startsWith("~/") || raw.startsWith("~\\")
    ? path.join(os.homedir(), raw.slice(1)) : raw;
  return path.resolve(expanded);
}

// Apps started from the Dock or Start menu do not get the shell PATH, so look in the usual places too.
function findDocker() {
  const exe = process.platform === "win32" ? "docker.exe" : "docker";
  const dirs = (process.env.PATH || "").split(path.delimiter).filter(Boolean);
  if (process.platform === "win32") {
    dirs.push(path.join(process.env.ProgramFiles || "C:\\Program Files", "Docker", "Docker", "resources", "bin"));
  } else {
    dirs.push("/usr/local/bin", "/opt/homebrew/bin", path.join(os.homedir(), ".docker", "bin"),
      "/Applications/Docker.app/Contents/Resources/bin", path.join(os.homedir(), ".orbstack", "bin"));
  }
  for (const dir of dirs) {
    const candidate = path.join(dir, exe);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // not here
    }
  }
  return null;
}

// Docker's helpers (docker-credential-desktop and others) sit next to docker, so put its folder on PATH.
function dockerEnv(docker) {
  return { ...process.env, PATH: [path.dirname(docker), process.env.PATH || ""].filter(Boolean).join(path.delimiter) };
}

// The first download is about 1 GB, longer than Claude waits for a server to start. Run it on its own,
// so it keeps going after Claude gives up on this launch, and log it in the folder.
function pullInBackground(docker, env, dir) {
  const pidFile = path.join(dir, "download.pid");
  try {
    // A pid file older than an hour is stale: the pull has ended, and its pid may belong to another process.
    if (Date.now() - fs.statSync(pidFile).mtimeMs < 60 * 60 * 1000) {
      process.kill(Number(fs.readFileSync(pidFile, "utf8")), 0);
      return; // a download from an earlier start is still running
    }
  } catch {
    // no download running
  }
  const log = fs.openSync(path.join(dir, "download.log"), "w");
  const pull = spawn(docker, ["pull", IMAGE], { detached: true, stdio: ["ignore", log, log], env, windowsHide: true });
  fs.closeSync(log);
  fs.writeFileSync(pidFile, String(pull.pid));
  pull.unref();
}

function main() {
  if (IMAGE.startsWith("__")) {
    fail("this extension was built without an image. Download deckwright.mcpb from the GitHub Release.");
  }
  const docker = findDocker();
  if (!docker) {
    fail("Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/, "
      + "start it, then restart Claude.");
  }
  const env = dockerEnv(docker);
  if (spawnSync(docker, ["info"], { stdio: "ignore", timeout: 20000, env }).status !== 0) {
    fail("Docker is not running. Start Docker Desktop, wait until it says it is running, then restart Claude.");
  }

  const dir = folder();
  if (/[,"]/.test(dir)) {
    // docker reads the --mount value as CSV.
    fail(`the Deckwright folder path cannot contain a comma or a double quote: ${dir}. `
      + "Pick another folder in the extension settings.");
  }
  try {
    for (const name of FOLDERS) fs.mkdirSync(path.join(dir, name), { recursive: true });
  } catch (err) {
    fail(`cannot create the Deckwright folder ${dir}: ${err.message}`);
  }

  if (spawnSync(docker, ["image", "inspect", IMAGE], { stdio: "ignore", env }).status !== 0) {
    pullInBackground(docker, env, dir);
    fail("Deckwright is downloading its image (about 1 GB, first start only). Wait a few minutes, then restart "
      + `Claude. Progress is in ${path.join(dir, "download.log")}.`);
  }
  for (const name of ["download.log", "download.pid"]) {
    fs.rmSync(path.join(dir, name), { force: true }); // the image is here, so the download notes are done
  }

  const args = [
    // --init: the server runs as PID 1 otherwise, which ignores SIGTERM.
    "run", "-i", "--rm", "--init", "--pull", "never", "--no-healthcheck",
    // Same hardening as the server image: read-only root, no capabilities, no privilege gain, limits.
    "--read-only", "--tmpfs", "/tmp:size=512m", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges:true", "--pids-limit", "512", "--memory", "2g",
    // The only folder the container can see. --mount, because -v misreads a Windows drive letter.
    "--mount", `type=bind,source=${dir},target=/data`,
    "-e", `DECKWRIGHT_HOST_DIR=${dir}`,
    "-e", `DECKWRIGHT_HOST_OS=${process.platform}`,
    "-e", `DECKWRIGHT_HOST_HOME=${os.homedir()}`,
    "-e", "DECKWRIGHT_OUTPUT_DIR=/data/Decks",
    "-e", "DECKWRIGHT_PACKS_DIR=/data/Templates",
    "--label", "deckwright=desktop",
  ];
  args.push(IMAGE, "deckwright", "mcp");

  const child = spawn(docker, args, { stdio: "inherit", env });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => child.kill(signal));
  }
  child.on("error", (err) => fail(`could not start Docker: ${err.message}`));
  child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
}

main();
