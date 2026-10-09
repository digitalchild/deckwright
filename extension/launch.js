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
  const raw = (process.env.DECKWRIGHT_FOLDER || "").trim() || path.join(os.homedir(), "Deckwright");
  const expanded = raw === "~" || raw.startsWith("~/") ? path.join(os.homedir(), raw.slice(1)) : raw;
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

function run(docker, args, timeout) {
  // Output goes to stderr, never stdout.
  return spawnSync(docker, args, { stdio: ["ignore", process.stderr, process.stderr], timeout });
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
  if (spawnSync(docker, ["info"], { stdio: "ignore", timeout: 20000 }).status !== 0) {
    fail("Docker is not running. Start Docker Desktop, wait until it says it is running, then restart Claude.");
  }

  const dir = folder();
  if (dir.includes(",")) {
    fail(`the Deckwright folder path cannot contain a comma: ${dir}. Pick another folder in the extension settings.`);
  }
  try {
    for (const name of FOLDERS) fs.mkdirSync(path.join(dir, name), { recursive: true });
  } catch (err) {
    fail(`cannot create the Deckwright folder ${dir}: ${err.message}`);
  }

  if (spawnSync(docker, ["image", "inspect", IMAGE], { stdio: "ignore" }).status !== 0) {
    say(`downloading ${IMAGE} (about 1 GB, first start only)...`);
    if (run(docker, ["pull", IMAGE], 30 * 60 * 1000).status !== 0) {
      fail("could not download the Deckwright image. Check your internet connection, then restart Claude.");
    }
  }

  const args = [
    "run", "-i", "--rm", "--pull", "never", "--no-healthcheck",
    // Same hardening as the server image: read-only root, no capabilities, no privilege gain, limits.
    "--read-only", "--tmpfs", "/tmp:size=512m", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges:true", "--pids-limit", "512", "--memory", "2g",
    // The only folder the container can see. --mount, because -v misreads a Windows drive letter.
    "--mount", `type=bind,source=${dir},target=/data`,
    "-e", `DECKWRIGHT_HOST_DIR=${dir}`,
    "-e", "DECKWRIGHT_OUTPUT_DIR=/data/Decks",
    "-e", "DECKWRIGHT_PACKS_DIR=/data/Templates",
    "--label", "deckwright=desktop",
  ];
  if (process.platform === "linux" && process.getuid) {
    // Linux bind mounts keep host ownership, so write files as the person, not as the image user.
    args.push("--user", `${process.getuid()}:${process.getgid()}`);
  }
  args.push(IMAGE, "deckwright", "mcp");

  const child = spawn(docker, args, { stdio: "inherit" });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => child.kill(signal));
  }
  child.on("error", (err) => fail(`could not start Docker: ${err.message}`));
  child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
}

main();
