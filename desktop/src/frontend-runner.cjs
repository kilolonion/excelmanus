// Next already has a graceful SIGTERM handler. Windows cannot deliver POSIX
// signals to a child, so translate the parent-owned stdin channel inside Node.
const readline = require('node:readline');
require(process.argv[2]);
let stopping = false;
function shutdown() {
  if (stopping) return;
  stopping = true;
  setTimeout(() => process.exit(1), 40_000).unref();
  const request = () => {
    if (process.listenerCount('SIGTERM')) process.emit('SIGTERM');
    else setTimeout(request, 100); // also handle a quit during Next startup
  };
  request();
}
const control = readline.createInterface({ input: process.stdin });
control.on('line', line => { if (line === 'shutdown') shutdown(); });
control.on('close', shutdown);
