from __future__ import annotations

from typing import Final


OPENCODE_CMDLINE_MATCHER_SCRIPT: Final = r'''classify_opencode_cmdline() {
    node - "$1" "$2" "$3" <<'NODE'
const fs = require("node:fs");

const [cmdlinePath, expectedHost, expectedPort] = process.argv.slice(2);
let cmdline;
try {
    cmdline = fs.readFileSync(cmdlinePath);
} catch {
    process.exit(2);
}
if (cmdline.length === 0 || cmdline[cmdline.length - 1] !== 0) {
    process.exit(2);
}

const argv = [];
let tokenStart = 0;
for (let index = 0; index < cmdline.length; index += 1) {
    if (cmdline[index] === 0) {
        argv.push(cmdline.subarray(tokenStart, index));
        tokenStart = index + 1;
    }
}

const bytes = (value) => Buffer.from(value, "utf8");
const equals = (token, value) => token !== undefined && token.equals(bytes(value));
const basename = (token) => {
    const slash = token.lastIndexOf(47);
    return token.subarray(slash + 1).toString("utf8");
};
const isRuntime = (token) => /^(?:bun|node|python|python3|python3\.\d+)$/.test(basename(token));

let commandIndex;
if (argv[0] !== undefined && basename(argv[0]) === "opencode") {
    commandIndex = 1;
} else if (argv[0] !== undefined && argv[1] !== undefined && isRuntime(argv[0]) && basename(argv[1]) === "opencode") {
    commandIndex = 2;
} else if (equals(argv[0], "/usr/bin/env") && argv[1] !== undefined && argv[2] !== undefined && isRuntime(argv[1]) && basename(argv[2]) === "opencode") {
    commandIndex = 3;
} else {
    process.exit(1);
}

if (!equals(argv[commandIndex], "serve") && !equals(argv[commandIndex], "web")) {
    process.exit(1);
}
const currentMatches =
    equals(argv[commandIndex + 1], "--hostname") &&
    equals(argv[commandIndex + 2], expectedHost) &&
    equals(argv[commandIndex + 3], "--port") &&
    equals(argv[commandIndex + 4], expectedPort);
if (currentMatches) {
    process.exit(0);
}
const legacyMatches =
    equals(argv[commandIndex + 1], "--pure") &&
    equals(argv[commandIndex + 2], "--hostname") &&
    equals(argv[commandIndex + 3], expectedHost) &&
    equals(argv[commandIndex + 4], "--port") &&
    equals(argv[commandIndex + 5], expectedPort);
process.exit(legacyMatches ? 3 : 1);
NODE
}

classify_process_activity() {
    node - "$1" <<'NODE'
const fs = require("node:fs");

let status;
try {
    status = fs.readFileSync(process.argv[2], "utf8");
} catch (error) {
    process.exit(error !== null && typeof error === "object" && error.code === "ENOENT" ? 1 : 2);
}
const match = /^State:\s+(\S+)(?:\s|$)/m.exec(status);
if (match === null || !/^[RSDZTtXxKWPI]$/.test(match[1])) {
    process.exit(2);
}
process.exit(match[1] === "Z" ? 1 : 0);
NODE
}
'''
