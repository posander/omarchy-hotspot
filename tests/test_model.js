const assert = require("node:assert/strict")
const model = require("../Model.js")

const adapter = {
  iface: "wlan0",
  apSupported: true,
  concurrent: true,
  bands: [
    { value: "bg", label: "2.4 GHz", channels: [1, 6, 11] },
    { value: "a", label: "5 GHz", channels: [36, 40] }
  ]
}

assert.equal(model.defaultBand(adapter), "bg")
assert.equal(model.defaultChannel(adapter, "bg"), "1")
assert.deepEqual(model.channelsFor(adapter, "a")[0], { value: "", label: "Auto" })
assert.equal(model.validateMac("02:11:22:33:44:55"), "")
assert.notEqual(model.validateMac("01:11:22:33:44:55"), "")
assert.equal(model.validatePassword("password123", "wpa-psk"), "")
assert.notEqual(model.validatePassword("short", "wpa-psk"), "")
assert.equal(model.validateSettings({
  ssid: "Temporary network",
  bssid: "02:11:22:33:44:55",
  security: "wpa-psk",
  password: "password123",
  band: "bg",
  channel: "6"
}, adapter), "")
assert.notEqual(model.validateSettings({
  ssid: "Temporary network",
  security: "wpa-psk",
  password: "password123",
  band: "a",
  channel: "11"
}, adapter), "")

console.log("model tests: ok")
