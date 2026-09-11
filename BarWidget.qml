import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Public bar-widget entry point. The panel is loaded separately so Quattro can
// route open/close/toggle requests through the standard widget contract.
BarWidget {
  id: root

  moduleName: "posander.omarchy-hotspot"

  readonly property var panelItem: panelLoader.item
  readonly property var panelStatus: panelItem
    ? panelItem.hotspotStatus
    : ({ active: false, expiresAt: 0 })
  readonly property bool hotspotActive: root.panelStatus.active === true
  readonly property string timerLabel: panelItem
    ? String(panelItem.remainingLabel || "")
    : ""
  readonly property bool timerVisible: root.hotspotActive
    && Number(root.panelStatus.expiresAt || 0) > 0
    && root.timerLabel !== ""

  implicitWidth: button.implicitWidth
    + (root.timerVisible ? Style.space(6) + dockTimer.implicitWidth : 0)
  implicitHeight: Math.max(button.implicitHeight, dockTimer.implicitHeight)

  readonly property bool opened: panelItem ? panelItem.opened === true : false
  readonly property bool popoutSwitchClosing: panelItem
    ? panelItem.popoutSwitchClosing === true
    : false

  function injectPanel() {
    if (!panelItem) return
    panelItem.bar = root.bar
    panelItem.anchorItem = button
    panelItem.hostWidget = root
  }

  function open() {
    if (panelItem) {
      panelItem.open()
      panelItem.refreshCapabilities()
      panelItem.refreshStatus()
    }
  }

  function close() {
    if (panelItem) panelItem.close()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function closeForPopoutSwitch() {
    if (panelItem && panelItem.closeForPopoutSwitch)
      panelItem.closeForPopoutSwitch()
  }

  onBarChanged: root.injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    bar: root.bar
    text: root.hotspotActive ? "󰖩" : "󰖪"
    active: root.hotspotActive
    tooltipText: root.hotspotActive
      ? "Hotspot: " + (root.panelStatus.ssid || "")
      : "Omarchy Wi-Fi Hotspot"
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.RightButton && panelItem)
        panelItem.stopHotspot()
      else
        root.toggle()
    }
  }

  Text {
    id: dockTimer
    anchors.left: button.right
    anchors.leftMargin: Style.space(6)
    anchors.verticalCenter: parent.verticalCenter
    visible: root.timerVisible
    textFormat: Text.PlainText
    text: root.timerLabel
    color: root.bar ? root.bar.foreground : Color.foreground
    font.family: root.bar ? root.bar.fontFamily : Style.font.family
    font.pixelSize: Style.font.caption
    font.bold: true
  }
}
