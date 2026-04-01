const { spawn } = require("child_process");

function runProcess(command, args, options = {}) {
  return new Promise((resolve) => {
    const timeoutMs = options.timeout || 60000; // varsayılan 60 saniye
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      shell: false
    });

    let stdout = "";
    let stderr = "";
    let settled = false;

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill("SIGTERM");
        resolve({ code: 1, stdout, stderr: `${stderr}\nİşlem ${timeoutMs}ms sonra zaman aşımına uğradı`.trim() });
      }
    }, timeoutMs);

    child.stdout.on("data", (d) => {
      stdout += String(d);
    });

    child.stderr.on("data", (d) => {
      stderr += String(d);
    });

    child.on("close", (code) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve({ code, stdout, stderr });
      }
    });

    child.on("error", (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve({ code: 1, stdout, stderr: `${stderr}\n${err.message}`.trim() });
      }
    });
  });
}

module.exports = { runProcess };

