const readline = require("node:readline");
const mode = process.argv[2];
if (mode === "stuck") setInterval(() => {}, 1000);
let initialized = false;
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  const message = JSON.parse(line);
  if (!initialized) {
    initialized = true;
    if (!message.token || !message.frontend_dist) process.exit(2);
    if (mode === "hang") return;
    if (mode === "invalid") console.log('{"type":"ready","port":-1}');
    else console.log('{"type":"ready","port":12345}');
    if (mode === "crash") setTimeout(() => process.exit(1), 100);
  } else if (message.type === "shutdown" && mode !== "stuck") process.exit(0);
});
