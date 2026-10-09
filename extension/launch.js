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

// A minimal MCP server (newline-delimited JSON-RPC over stdio) with one tool, setup_status. status() returns
// the current text, so a later call can report that the download has finished.
function setupServer(status) {
  say(status());
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
          instructions: `Deckwright is not ready yet. ${status()} When the user asks for Deckwright or a deck, `
            + "call setup_status and tell them what it says, in plain words.",
        };
      case "ping":
        return {};
      case "tools/list":
        return { tools: [tool] };
      case "tools/call":
        if (!req.params || req.params.name !== tool.name) {
          return { error: { code: -32602, message: `unknown tool; Deckwright has only ${tool.name} until it is ready.` } };
        }
        return { content: [{ type: "text", text: status() }] };
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
        : result.error ? result : { result };
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: req.id, ...reply }) + "\n");
    }
  });
  process.stdin.on("end", () => process.exit(0));
}

function imagePresent(docker, env) {
  return spawnSync(docker, ["image", "inspect", IMAGE], { stdio: "ignore", env }).status === 0;
}

let lastFailure = ""; // why the previous download ended without the image, kept for every later status

function downloadStatus(docker, env, dir) {
  if (imagePresent(docker, env)) return "The download is done. Quit Claude completely and open it again to start Deckwright.";
  lastFailure = pullInBackground(docker, env, dir) || lastFailure; // starts a new download only when none is running
  return (lastFailure ? `The last download did not finish (${lastFailure}), so Deckwright started it again. ` : "")
    + "Deckwright is downloading its image (about 1 GB, first start only). Wait a few minutes, then quit Claude "
    + `completely and open it again. Progress is in ${path.join(dir, "download.log")}.`;
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
// so it keeps going after Claude gives up on this launch, and log it in the folder. Returns the last line
// of an earlier download that ended without the image, so the person can see why.
function pullInBackground(docker, env, dir) {
  const pidFile = path.join(dir, "download.pid");
  const logFile = path.join(dir, "download.log");
  try {
    // A pid file older than an hour is stale: the pull has ended, and its pid may belong to another process.
    if (Date.now() - fs.statSync(pidFile).mtimeMs < 60 * 60 * 1000) {
      process.kill(Number(fs.readFileSync(pidFile, "utf8")), 0);
      return ""; // a download from an earlier start is still running
    }
  } catch {
    // no download running
  }
  let failed = "";
  try {
    failed = fs.readFileSync(logFile, "utf8").trim().split("\n").pop();
  } catch {
    // no earlier download
  }
  const log = fs.openSync(logFile, "w");
  const pull = spawn(docker, ["pull", IMAGE], { detached: true, stdio: ["ignore", log, log], env, windowsHide: true });
  fs.closeSync(log);
  pull.on("error", () => {}); // the message below already tells the person to check the log
  if (pull.pid) fs.writeFileSync(pidFile, String(pull.pid));
  pull.unref();
  return failed;
}

function main() {
  if (IMAGE.startsWith("__")) {
    return setupServer(() => "This extension was built without an image. Download deckwright.mcpb from the "
      + "Deckwright GitHub Release and install it again.");
  }
  const docker = findDocker();
  if (!docker) {
    return setupServer(() => "Docker Desktop is not installed. Install it from "
      + "https://www.docker.com/products/docker-desktop/, start it, then quit Claude completely and open it again.");
  }
  const env = dockerEnv(docker);
  if (spawnSync(docker, ["info"], { stdio: "ignore", timeout: 20000, env }).status !== 0) {
    return setupServer(() => "Docker Desktop is not running. Start it, wait until it says it is running, then quit "
      + "Claude completely and open it again.");
  }

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
    return setupServer(() => downloadStatus(docker, env, dir));
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
