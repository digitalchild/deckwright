// Claude Desktop extension launcher: runs the Deckwright MCP server in Docker over stdio.
//
// Claude Desktop starts this script with its own Node.js. It finds Docker, pulls the image on first use,
// then runs the container with the person's Deckwright folder mounted at /data. stdout carries the MCP
// protocol, so every message from this script goes to stderr.
//
// When Deckwright cannot start yet (no Docker, or the first download is still running), this script serves
// a small MCP server itself, so Claude can tell the person what to do instead of showing "Server disconnected".
//
// No dependencies: Node.js built-ins only.

"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

// The release build replaces this with the image pinned by digest. DECKWRIGHT_IMAGE is for testing the
// unbuilt launcher only, so nothing in the environment can change the image a built extension runs.
const BUILT_IMAGE = "__IMAGE__";
const IMAGE = BUILT_IMAGE.startsWith("__") ? process.env.DECKWRIGHT_IMAGE || BUILT_IMAGE : BUILT_IMAGE;
const FOLDERS = ["Decks", "Templates", "Inbox"];

function say(message) {
  process.stderr.write(`deckwright: ${message}\n`);
}

function fail(message) {
  say(message);
  process.exit(1);
}

const AGAIN = "quit Claude completely and open it again";

// A minimal MCP server (newline-delimited JSON-RPC over stdio) with one tool, setup_status. status() checks
// again on each call, so the tool can report that Docker now runs or that the download has finished.
function setupServer(status, first = status()) {
  say(first);
  const tool = {
    name: "setup_status",
    description: "Deckwright is not ready yet. Call this to learn why and what the user must do, then tell the user.",
    inputSchema: { type: "object", properties: {} },
  };
  const answer = (req) => {
    switch (req.method) {
      case "initialize":
        return {
          protocolVersion: (req.params && req.params.protocolVersion) || "2025-06-18",
          capabilities: { tools: {} },
          serverInfo: { name: "deckwright", version: "setup" },
          instructions: `Deckwright is not ready yet. ${first} When the user asks for Deckwright or a deck, call `
            + "setup_status (it checks again) and tell them what it says, in plain words.",
        };
      case "ping":
        return {};
      case "tools/list":
        return { tools: [tool] };
      case "tools/call": {
        const name = req.params && req.params.name;
        if (name === tool.name) return { content: [{ type: "text", text: status() }] };
        // A tool from an earlier session: say why it is missing instead of a bare protocol error.
        return { content: [{ type: "text", text: `Deckwright is not ready yet, so ${name} is not available. `
          + status() }], isError: true };
      }
      case "prompts/list":
        return { prompts: [] };
      case "resources/list":
        return { resources: [] };
      case "resources/templates/list":
        return { resourceTemplates: [] };
      default:
        return undefined;
    }
  };
  let buffer = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end).trim();
      buffer = buffer.slice(end + 1);
      let req;
      try {
        req = JSON.parse(line);
      } catch {
        continue;
      }
      if (!req || req.id === undefined || req.id === null) continue; // a notification needs no answer
      const result = answer(req);
      const reply = result === undefined
        ? { error: { code: -32601, message: `Deckwright is not ready yet, so ${req.method} is not available.` } }
        : { result };
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: req.id, ...reply }) + "\n");
    }
  });
  process.stdin.on("end", () => process.exit(0));
  process.stdout.on("error", () => process.exit(0)); // Claude closed the connection
}

// Every Docker call has a time limit, so a stuck Docker cannot freeze the setup server.
function imagePresent(docker, env) {
  return spawnSync(docker, ["image", "inspect", IMAGE], { stdio: "ignore", timeout: 10000, env }).status === 0;
}

function dockerRunning(docker, env) {
  return spawnSync(docker, ["info"], { stdio: "ignore", timeout: 20000, env }).status === 0;
}

const DOCKER_STOPPED = `Docker Desktop is not running. Start it, wait until it says it is running, then ${AGAIN}.`;

function dockerStatus() {
  const docker = findDocker();
  if (!docker) {
    return "Docker Desktop is not installed. Install it from https://www.docker.com/products/docker-desktop/, "
      + `start it, then ${AGAIN}.`;
  }
  if (!dockerRunning(docker, dockerEnv(docker))) return DOCKER_STOPPED;
  return `Docker Desktop is running now. To start Deckwright, ${AGAIN}.`;
}

function lastLine(file) {
  try {
    return fs.readFileSync(file, "utf8").trim().split("\n").pop().trim();
  } catch {
    return "";
  }
}

// earlier: why a download from an earlier start ended without the image ("" when there was none).
function downloadStatus(docker, env, dir, earlier) {
  if (imagePresent(docker, env)) return `The download is done. To start Deckwright, ${AGAIN}.`;
  const log = path.join(dir, "download.log");
  if (!pullRunning(dir)) {
    if (!dockerRunning(docker, env)) return DOCKER_STOPPED; // Docker quit, not the network
    const why = lastLine(log);
    return `The download stopped${why ? ` (${why})` : ""}. Check your internet connection, then ${AGAIN} to try again.`;
  }
  return (earlier ? `The last download did not finish (${earlier}), so Deckwright started it again. ` : "")
    + `Deckwright is downloading its image (about 1 GB, first start only). Wait a few minutes, then ${AGAIN}. `
    + `Progress is in ${log}.`;
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
const pull = { child: null, exited: false }; // the download this launch started, if any

// A pid file older than this is stale: the pull has ended, and its pid may belong to another process.
const PULL_MAX_AGE_MS = 60 * 60 * 1000;

function pullRunning(dir) {
  if (pull.child) return !pull.exited;
  const pidFile = path.join(dir, "download.pid");
  try {
    if (Date.now() - fs.statSync(pidFile).mtimeMs >= PULL_MAX_AGE_MS) return false;
    process.kill(Number(fs.readFileSync(pidFile, "utf8")), 0);
    return true; // a download from an earlier start is still running
  } catch {
    return false;
  }
}

// Starts the download unless one is running. Returns the last line of an earlier download that ended
// without the image, so the person can see why.
function pullInBackground(docker, env, dir) {
  if (pullRunning(dir)) return "";
  const logFile = path.join(dir, "download.log");
  const failed = lastLine(logFile);
  const log = fs.openSync(logFile, "w");
  const child = spawn(docker, ["pull", IMAGE], { detached: true, stdio: ["ignore", log, log], env, windowsHide: true });
  fs.closeSync(log);
  const pidFile = path.join(dir, "download.pid");
  const ended = () => {
    pull.exited = true;
    fs.rmSync(pidFile, { force: true }); // so a later start cannot mistake a reused pid for this download
  };
  pull.child = child;
  child.on("error", ended);
  child.on("exit", ended);
  if (child.pid) fs.writeFileSync(pidFile, String(child.pid));
  child.unref();
  return failed;
}

function main() {
  if (IMAGE.startsWith("__")) {
    return setupServer(() => "This extension was built without an image. Download deckwright.mcpb from the "
      + "Deckwright GitHub Release and install it again.");
  }
  const docker = findDocker();
  if (!docker) return setupServer(dockerStatus);
  // The first answer reuses this check, so a stuck Docker delays the reply to Claude only once.
  if (!dockerRunning(docker, dockerEnv(docker))) return setupServer(dockerStatus, DOCKER_STOPPED);
  const env = dockerEnv(docker);

  const dir = folder();
  if (/[,"]/.test(dir)) {
    // docker reads the --mount value as CSV.
    return setupServer(() => `The Deckwright folder path cannot contain a comma or a double quote: ${dir}. `
      + "Pick another folder in Claude's extension settings for Deckwright.");
  }
  try {
    for (const name of FOLDERS) fs.mkdirSync(path.join(dir, name), { recursive: true });
  } catch (err) {
    return setupServer(() => `Deckwright cannot create its folder ${dir} (${err.message}). Pick another folder in `
      + "Claude's extension settings for Deckwright.");
  }

  if (!imagePresent(docker, env)) {
    const earlier = pullInBackground(docker, env, dir);
    return setupServer(() => downloadStatus(docker, env, dir, earlier));
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

  // Pipe the streams through this process. Claude Desktop runs this script in an Electron utility
  // process, where stdin is not a real file descriptor, so a child that inherits it reads nothing.
  const child = spawn(docker, args, { stdio: ["pipe", "pipe", "pipe"], env });
  process.stdin.pipe(child.stdin);
  child.stdout.pipe(process.stdout);
  child.stderr.pipe(process.stderr);
  child.stdin.on("error", () => {}); // the container exited; the exit handler reports it
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => child.kill(signal));
  }
  child.on("error", (err) => fail(`could not start Docker: ${err.message}`));
  child.on("exit", (code, signal) => {
    if (code) say(`the container stopped with exit code ${code}.`);
    process.exitCode = code ?? (signal ? 1 : 0);
  });
  // Exit once the container's output is flushed, so the last reply is not lost.
  child.on("close", () => process.exit());
}

main();
