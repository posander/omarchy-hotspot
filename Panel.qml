import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root

  moduleName: "posander.omarchy-hotspot"
  manageIpc: false

  // The bar entry point owns the icon. These properties let the panel use the
  // canonical Omarchy bar-widget contract without duplicating the button.
  property var anchorItem: null
  property var hostWidget: null

  readonly property color foreground: root.bar ? root.bar.foreground : Color.foreground
  readonly property string backendPath: decodeURIComponent(String(Qt.resolvedUrl("bin/hotspot_backend.py")).replace(/^file:\/\//, ""))
  readonly property var currentAdapter: Model.findAdapter(root.capabilities.adapters, root.selectedIface)
  readonly property var adapterOptions: Model.adapterOptions(root.capabilities.adapters)
  readonly property var bandOptions: Model.bandOptions(root.currentAdapter)
  readonly property var channelOptions: Model.channelsFor(root.currentAdapter, root.band)

  property var capabilities: ({ adapters: [], uplinks: [], errors: [], networkManager: false })
  property var dependencyStatus: ({ ready: false, missing: [] })
  property var hotspotStatus: ({ active: false, expiresAt: 0 })
  property bool busy: false
  property bool dependencyCheckInFlight: false
  property bool statusInFlight: false
  property bool capabilitiesInFlight: false
  property bool preferencesLoaded: false
  property string errorMessage: ""
  property string infoMessage: ""

  property string selectedIface: ""
  property string ssid: "Omarchy Wi-Fi Hotspot"
  property string bssid: ""
  property bool hidden: false
  property string security: "wpa-psk"
  property string password: ""
  property string band: ""
  property string channel: ""
  property string duration: "3600"
  property real clockTick: Date.now() / 1000

  readonly property bool dependenciesReady: root.dependencyStatus.ready === true
  readonly property bool dependencyInstallRunning: backend.installingDependencies
  readonly property string missingDependencyNames: {
    var missing = root.dependencyStatus.missing || []
    var names = []
    for (var index = 0; index < missing.length; index++)
      names.push(String(missing[index].label || missing[index].package || "unknown"))
    return names.join(", ")
  }

  onSelectedIfaceChanged: root.schedulePreferencesSave()
  onSsidChanged: root.schedulePreferencesSave()
  onBssidChanged: root.schedulePreferencesSave()
  onHiddenChanged: root.schedulePreferencesSave()
  onSecurityChanged: root.schedulePreferencesSave()
  onPasswordChanged: root.schedulePreferencesSave()
  onBandChanged: root.schedulePreferencesSave()
  onChannelChanged: root.schedulePreferencesSave()
  onDurationChanged: root.schedulePreferencesSave()

  readonly property var securityOptions: [
    { value: "wpa-psk", label: "WPA2-Personal (compatible)" },
    { value: "open", label: "Open (no password)" }
  ]
  readonly property var durationOptions: [
    { value: "900", label: "15 minutes" },
    { value: "1800", label: "30 minutes" },
    { value: "3600", label: "1 hour" },
    { value: "7200", label: "2 hours" },
    { value: "14400", label: "4 hours" },
    { value: "0", label: "No timer" }
  ]

  readonly property real remainingSeconds: {
    var expires = Number(root.hotspotStatus.expiresAt || 0)
    return expires > 0 ? Math.max(0, expires - root.clockTick) : 0
  }
  readonly property string remainingLabel: root.hotspotStatus.active
    ? (Number(root.hotspotStatus.expiresAt || 0) > 0
      ? Model.formatRemaining(root.remainingSeconds)
      : "No timer")
    : ""
  readonly property string statusText: root.hotspotStatus.active
    ? "Active · " + root.hotspotStatus.ssid
    : (root.busy ? "Working…" : "Off")
  readonly property bool formBlocked: ssidField.activeFocus
    || bssidField.activeFocus
    || passwordField.activeFocus
    || adapterDropdown.popupOpen
    || bandDropdown.popupOpen
    || channelDropdown.popupOpen
    || securityDropdown.popupOpen
    || durationDropdown.popupOpen

  Backend {
    id: backend
    scriptPath: root.backendPath
    onLogMessage: function(message) {
      if (message.indexOf("Traceback") === 0) root.errorMessage = message
    }
  }

  Connections {
    target: backend
    function onDependencyInstallFinished(exitCode, output) {
      if (exitCode === 0) {
        root.errorMessage = ""
        root.infoMessage = "Dependencies installed. Checking again…"
        root.refreshDependencies()
        root.refreshCapabilities()
      } else {
        var lines = String(output || "").trim().split("\n")
        var detail = lines.length && lines[lines.length - 1] ? ": " + lines[lines.length - 1] : ""
        root.errorMessage = "Dependency installation was cancelled or failed" + detail
        root.infoMessage = ""
      }
    }
  }

  function setFormDefaults() {
    var adapter = Model.findAdapter(root.capabilities.adapters, root.selectedIface)
    if (!adapter) {
      root.selectedIface = ""
      root.band = ""
      root.channel = ""
      return
    }

    if (root.selectedIface !== adapter.iface)
      root.selectedIface = String(adapter.iface || "")

    var bands = Model.bandOptions(adapter)
    var bandValid = bands.some(function(option) { return option.value === root.band })
    if (!bandValid) root.band = Model.defaultBand(adapter)

    var channels = Model.channelsFor(adapter, root.band).map(function(option) { return option.value })
    if (channels.indexOf(String(root.channel)) === -1)
      root.channel = Model.defaultChannel(adapter, root.band)
  }

  function refreshDependencies() {
    if (root.dependencyCheckInFlight || root.dependencyInstallRunning) return
    root.dependencyCheckInFlight = true
    backend.send("dependencies", {}, function(data, error) {
      root.dependencyCheckInFlight = false
      if (error) {
        root.dependencyStatus = ({ ready: false, missing: [] })
        root.errorMessage = error
        return
      }
      root.dependencyStatus = data || ({ ready: false, missing: [] })
    })
  }

  function installDependencies() {
    if (root.dependencyInstallRunning || root.dependenciesReady) return
    root.errorMessage = ""
    root.infoMessage = "Installing required components…"
    backend.installDependencies()
  }

  function refreshCapabilities() {
    if (root.capabilitiesInFlight) return
    root.capabilitiesInFlight = true
    backend.send("capabilities", {}, function(data, error) {
      root.capabilitiesInFlight = false
      if (error) {
        root.errorMessage = error
        return
      }
      root.capabilities = data || ({ adapters: [], uplinks: [], errors: [], networkManager: false })
      root.setFormDefaults()
    })
  }

  function refreshStatus() {
    if (root.statusInFlight) return
    root.statusInFlight = true
    backend.send("status", {}, function(data, error) {
      root.statusInFlight = false
      if (error) {
        root.errorMessage = error
        return
      }
      root.hotspotStatus = data || ({ active: false, expiresAt: 0 })
      if (root.hotspotStatus.active) root.infoMessage = ""
      else if (root.hotspotStatus.expired) root.infoMessage = "The timer stopped the hotspot"
    })
  }

  function preferencesSnapshot() {
    return {
      iface: root.selectedIface,
      ssid: root.ssid,
      bssid: root.bssid,
      hidden: root.hidden,
      security: root.security,
      password: root.password,
      band: root.band,
      channel: root.channel,
      duration: root.duration
    }
  }

  function loadPreferences() {
    backend.send("loadPreferences", {}, function(data, requestError) {
      if (requestError) {
        root.errorMessage = requestError
        root.preferencesLoaded = true
        return
      }
      var value = data || ({})
      root.preferencesLoaded = false
      if (value.iface !== undefined) root.selectedIface = String(value.iface)
      if (value.ssid !== undefined) root.ssid = String(value.ssid)
      if (value.bssid !== undefined) root.bssid = String(value.bssid)
      if (value.hidden !== undefined) root.hidden = !!value.hidden
      if (value.security !== undefined) root.security = String(value.security)
      if (value.password !== undefined) root.password = String(value.password)
      if (value.band !== undefined) root.band = String(value.band)
      if (value.channel !== undefined) root.channel = String(value.channel)
      if (value.duration !== undefined) root.duration = String(value.duration)
      root.preferencesLoaded = true
    })
  }

  function schedulePreferencesSave() {
    if (!root.preferencesLoaded) return
    preferencesSaveTimer.restart()
  }

  function persistPreferences() {
    if (!root.preferencesLoaded) return
    backend.send("savePreferences", { settings: root.preferencesSnapshot() }, function(data, requestError) {
      if (requestError) root.errorMessage = requestError
    })
  }

  function startHotspot() {
    var settings = {
      iface: root.selectedIface,
      ssid: root.ssid,
      bssid: root.bssid,
      hidden: root.hidden,
      security: root.security,
      password: root.password,
      band: root.band,
      channel: root.channel
    }
    var error = Model.validateSettings(settings, root.currentAdapter)
    if (error) {
      root.errorMessage = error
      return
    }

    var seconds = Number(root.duration)
    if (!Number.isFinite(seconds) || seconds < 0) {
      root.errorMessage = "Invalid timer duration"
      return
    }

    root.busy = true
    root.errorMessage = ""
    root.infoMessage = "Starting hotspot…"
    backend.send("start", { settings: settings, duration: Math.floor(seconds) }, function(data, requestError) {
      root.busy = false
      if (requestError) {
        root.errorMessage = requestError
        root.infoMessage = ""
        root.refreshStatus()
        return
      }
      root.hotspotStatus = data || ({ active: false, expiresAt: 0 })
      root.infoMessage = root.hotspotStatus.active ? "Hotspot is ready" : "Hotspot did not become active"
    })
  }

  function stopHotspot() {
    root.busy = true
    root.errorMessage = ""
    root.infoMessage = "Stopping hotspot…"
    backend.send("stop", {}, function(data, requestError) {
      root.busy = false
      if (requestError) {
        root.errorMessage = requestError
        root.infoMessage = ""
        return
      }
      root.hotspotStatus = ({ active: false, expiresAt: 0 })
      root.infoMessage = "Hotspot disabled"
    })
  }

  Timer {
    interval: 1000
    running: true
    repeat: true
    onTriggered: {
      root.clockTick = Date.now() / 1000
      if (root.hotspotStatus.active) root.refreshStatus()
    }
  }

  Timer {
    id: preferencesSaveTimer
    interval: 400
    repeat: false
    onTriggered: root.persistPreferences()
  }

  Component.onCompleted: {
    root.refreshDependencies()
    root.refreshCapabilities()
    root.refreshStatus()
    root.loadPreferences()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(520))
    contentHeight: panel.fittedContentHeight(hotspotColumn.implicitHeight, Style.space(760))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.formBlocked
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: hotspotColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: hotspotColumn
          width: scroll.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            visible: root.dependenciesReady
            height: visible ? implicitHeight : 0
            iconComponent: Component {
              Text {
                text: root.hotspotStatus.active ? "󰖩" : "󰖪"
                color: root.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.display
              }
            }
            title: root.hotspotStatus.active ? root.hotspotStatus.ssid : "Omarchy Wi-Fi Hotspot"
            meta: root.statusText
            detail: root.hotspotStatus.active ? "ON" : "OFF"
            trailingControl: Component {
              ToggleSwitch {
                checked: root.hotspotStatus.active
                busy: root.busy || root.dependencyInstallRunning
                enabled: root.dependenciesReady
                onToggled: root.hotspotStatus.active ? root.stopHotspot() : root.startHotspot()
              }
            }
          }

          Column {
            width: parent.width
            visible: !root.dependenciesReady
            height: visible ? implicitHeight : 0
            spacing: Style.space(8)

            Text {
              width: parent.width
              text: "Omarchy Wi-Fi Hotspot"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: "The hotspot needs NetworkManager, dnsmasq, iw, and Python D-Bus for adapter detection, DHCP, and internet sharing."
              color: Qt.darker(root.foreground, 1.35)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.dependencyInstallRunning
                ? "Installing required components…"
                : root.dependencyCheckInFlight
                  ? "Checking required components…"
                  : root.missingDependencyNames !== ""
                    ? "Missing: " + root.missingDependencyNames
                    : "Dependency check failed. Press Refresh to try again."
              color: root.dependencyInstallRunning ? Qt.darker(root.foreground, 1.35) : Color.urgent
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Button {
              width: parent.width
              text: root.dependencyInstallRunning ? "Installing…" : "Install dependencies"
              iconText: "󰒓"
              enabled: !root.dependencyCheckInFlight && !root.dependencyInstallRunning && !root.busy && !root.dependenciesReady
              onClicked: root.installDependencies()
            }

            Text {
              width: parent.width
              visible: root.errorMessage !== ""
              textFormat: Text.PlainText
              text: root.errorMessage
              color: Color.urgent
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Text {
              width: parent.width
              visible: root.infoMessage !== ""
              textFormat: Text.PlainText
              text: root.infoMessage
              color: Qt.darker(root.foreground, 1.35)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }

          Column {
            id: mainInterface
            width: parent.width
            visible: root.dependenciesReady
            height: visible ? implicitHeight : 0
            spacing: Style.space(12)

          Text {
            width: parent.width
            visible: root.hotspotStatus.active
            textFormat: Text.PlainText
            text: root.hotspotStatus.active
              ? "BSSID " + (root.hotspotStatus.bssid || "automatic")
                + (root.hotspotStatus.ip ? " · " + root.hotspotStatus.ip : "")
                + " · " + root.hotspotStatus.clients + " clients"
              : ""
            color: Qt.darker(root.foreground, 1.35)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }

          PanelSeparator { width: parent.width }
          PanelSectionHeader { text: "ADAPTER"; foreground: root.foreground }

          Dropdown {
            id: adapterDropdown
            width: parent.width
            label: "Wi-Fi adapter"
            value: root.selectedIface
            options: root.adapterOptions.length ? root.adapterOptions : [{ value: "", label: "No Wi-Fi adapter detected" }]
            enabled: !root.busy && root.adapterOptions.length > 0
            onChanged: function(value) {
              root.selectedIface = value
              root.band = Model.defaultBand(root.currentAdapter)
              root.channel = Model.defaultChannel(root.currentAdapter, root.band)
            }
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: root.currentAdapter
              ? Model.capabilitySummary(root.currentAdapter)
              : (root.capabilities.errors.length ? root.capabilities.errors.join(" · ") : "Detecting Wi-Fi capabilities…")
            color: root.currentAdapter && !root.currentAdapter.apSupported ? Color.urgent : Qt.darker(root.foreground, 1.45)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Text {
            width: parent.width
            visible: !!root.currentAdapter && !root.currentAdapter.concurrent
            textFormat: Text.PlainText
            text: "This adapter does not report simultaneous station + AP mode. Starting the hotspot may disconnect the current Wi-Fi uplink."
            color: Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          PanelSeparator { width: parent.width }
            PanelSectionHeader { text: "OMARCHY WI-FI HOTSPOT SETTINGS"; foreground: root.foreground }

          Column {
            width: parent.width
            spacing: Style.spacing.labelGap
            Text {
              text: "Network name (SSID)"
              color: Qt.darker(root.foreground, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            TextField {
              id: ssidField
              width: parent.width
              text: root.ssid
              placeholderText: "Omarchy Wi-Fi Hotspot"
              onTextChanged: root.ssid = text
            }
          }

          Column {
            width: parent.width
            spacing: Style.spacing.labelGap
            Text {
              text: "BSSID / AP MAC (optional)"
              color: Qt.darker(root.foreground, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            TextField {
              id: bssidField
              width: parent.width
              text: root.bssid
              placeholderText: "Automatic"
              onTextChanged: root.bssid = text
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(10)

            Dropdown {
              id: bandDropdown
              width: (parent.width - parent.spacing) / 2
              label: "Band"
              value: root.band
              options: root.bandOptions.length ? root.bandOptions : [{ value: "", label: "Unavailable" }]
              enabled: !root.busy && root.bandOptions.length > 0
              onChanged: function(value) {
                root.band = value
                root.channel = Model.defaultChannel(root.currentAdapter, root.band)
              }
            }

            Dropdown {
              id: channelDropdown
              width: (parent.width - parent.spacing) / 2
              label: "Channel"
              value: root.channel
              options: root.channelOptions
              enabled: !root.busy && root.bandOptions.length > 0
              onChanged: function(value) { root.channel = value }
            }
          }

          Toggle {
            width: parent.width
            label: "Hidden network"
            description: "Do not advertise the SSID in beacon frames"
            checked: root.hidden
            onClicked: root.hidden = !root.hidden
          }

          Dropdown {
            id: securityDropdown
            width: parent.width
            label: "Protection"
            value: root.security
            options: root.securityOptions
            enabled: !root.busy
            onChanged: function(value) { root.security = value }
          }

          Column {
            width: parent.width
            visible: root.security !== "open"
            spacing: Style.spacing.labelGap
            Text {
              text: "Password"
              color: Qt.darker(root.foreground, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            TextField {
              id: passwordField
              width: parent.width
              text: root.password
              password: true
              placeholderText: "8–63 characters"
              onTextChanged: root.password = text
            }
          }

          Dropdown {
            id: durationDropdown
            width: parent.width
            label: "Automatically disable after"
            value: root.duration
            options: root.durationOptions
            enabled: !root.busy
            onChanged: function(value) { root.duration = value }
          }

          Text {
            width: parent.width
            visible: root.hotspotStatus.active && root.remainingLabel !== ""
            textFormat: Text.PlainText
            text: root.hotspotStatus.active
              ? (root.remainingLabel === "No timer" ? "Timer: no automatic shutdown" : "Timer: " + root.remainingLabel)
              : ""
            color: Qt.darker(root.foreground, 1.3)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            Button {
              text: root.hotspotStatus.active ? "Apply & restart" : "Enable hotspot"
              iconText: "󰐊"
              enabled: !root.busy && !root.dependencyInstallRunning && root.dependenciesReady && !!root.currentAdapter && root.currentAdapter.apSupported
              onClicked: root.startHotspot()
            }

            Button {
              text: "Disable"
              iconText: "󰙧"
              enabled: !root.busy && root.hotspotStatus.active
              bordered: true
              onClicked: root.stopHotspot()
            }

            Button {
              text: "Refresh"
              iconText: "󰑐"
              enabled: !root.busy && !root.dependencyInstallRunning
              bordered: true
              onClicked: { root.refreshDependencies(); root.refreshCapabilities(); root.refreshStatus() }
            }
          }

          Text {
            width: parent.width
            visible: root.errorMessage !== ""
            textFormat: Text.PlainText
            text: root.errorMessage
            color: Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Text {
            width: parent.width
            visible: root.infoMessage !== ""
            textFormat: Text.PlainText
            text: root.infoMessage
            color: Qt.darker(root.foreground, 1.35)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }
          }
        }
      }
    }
  }
}
