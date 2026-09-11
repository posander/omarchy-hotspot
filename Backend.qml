import QtQuick
import Quickshell.Io

// A small JSON-lines bridge keeps privileged-ish NetworkManager operations and
// capability parsing out of the UI. Secrets are sent over stdin to this
// process and never become command-line arguments.
QtObject {
  id: root

  required property string scriptPath
  property bool desiredRunning: true
  property int serial: 0
  property var pending: ({})
  property var queued: []
  property string dependencyInstallOutput: ""
  readonly property bool installingDependencies: dependencyInstaller.running

  signal logMessage(string message)
  signal dependencyInstallFinished(int exitCode, string output)

  function start() {
    if (!process.running) process.running = true
  }

  function stop() {
    root.desiredRunning = false
    root.queued = []
    if (process.running) process.running = false
  }

  // Installation is deliberately explicit and uses a fixed package list.
  // pkexec shows the system authentication prompt; no shell or user-provided
  // command is involved.
  function installDependencies() {
    if (!dependencyInstaller.running) dependencyInstaller.running = true
  }

  function send(command, fields, callback) {
    var id = String(++root.serial)
    var request = { id: id, command: String(command) }
    var values = fields || {}
    for (var key in values) request[key] = values[key]

    root.pending[id] = callback || function() {}
    var line = JSON.stringify(request) + "\n"
    if (process.running) process.write(line)
    else {
      root.queued.push(line)
      root.start()
    }
    return id
  }

  function flush() {
    while (root.queued.length > 0 && process.running)
      process.write(root.queued.shift())
  }

  function failPending(message) {
    var requests = root.pending
    root.pending = ({})
    for (var id in requests) requests[id](null, message)
  }

  function handleLine(line) {
    var reply
    try {
      reply = JSON.parse(String(line || ""))
    } catch (error) {
      root.failPending("Hotspot backend returned invalid JSON")
      return
    }

    if (!reply || !reply.id) return
    var callback = root.pending[reply.id]
    if (!callback) return
    delete root.pending[reply.id]
    callback(reply.ok ? (reply.data || {}) : null,
             reply.ok ? "" : String(reply.error || "Request failed"))
  }

  property Process process: Process {
    command: ["python3", root.scriptPath]
    stdinEnabled: true

    stdout: SplitParser {
      onRead: function(line) { root.handleLine(line) }
    }

    stderr: SplitParser {
      onRead: function(line) {
        var message = String(line || "").trim()
        if (message !== "") root.logMessage(message)
      }
    }

    onStarted: root.flush()
    onExited: function(exitCode) {
      if (!root.desiredRunning) {
        root.failPending("Hotspot backend stopped")
        return
      }
      if (exitCode !== 0) root.logMessage("Hotspot backend exited with code " + exitCode)
      Qt.callLater(function() {
        if (root.desiredRunning && !process.running) process.running = true
      })
    }
  }

  property Process dependencyInstaller: Process {
    id: dependencyInstaller
    command: [
      "/usr/bin/pkexec",
      "/usr/bin/pacman",
      "-S",
      "--needed",
      "--noconfirm",
      "networkmanager",
      "dnsmasq",
      "iw",
      "python-dbus"
    ]

    stdout: SplitParser {
      onRead: function(line) {
        root.dependencyInstallOutput += String(line || "") + "\n"
      }
    }

    stderr: SplitParser {
      onRead: function(line) {
        root.dependencyInstallOutput += String(line || "") + "\n"
      }
    }

    onStarted: root.dependencyInstallOutput = ""
    onExited: function(exitCode) {
      root.dependencyInstallFinished(exitCode, root.dependencyInstallOutput.trim())
    }
  }

  Component.onCompleted: root.start()
}
