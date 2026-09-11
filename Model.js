function asString(value) {
  return value === undefined || value === null ? "" : String(value)
}

function adapterOptions(adapters) {
  var values = Array.isArray(adapters) ? adapters : []
  return values.map(function(adapter) {
    var bands = Array.isArray(adapter.bands) ? adapter.bands.map(function(band) {
      return band.label || band.value
    }).join(", ") : "none"
    var ap = adapter.apSupported ? "AP" : "no AP"
    var concurrent = adapter.concurrent ? " · STA+AP" : ""
    return {
      value: asString(adapter.iface),
      label: asString(adapter.iface) + " · " + ap + concurrent + " · " + bands
    }
  })
}

function bandOptions(adapter) {
  var bands = adapter && Array.isArray(adapter.bands) ? adapter.bands : []
  return bands.map(function(band) {
    return { value: asString(band.value), label: asString(band.label || band.value) }
  })
}

function channelsFor(adapter, band) {
  var bands = adapter && Array.isArray(adapter.bands) ? adapter.bands : []
  for (var i = 0; i < bands.length; i++) {
    if (asString(bands[i].value) === asString(band)) {
      var channels = Array.isArray(bands[i].channels) ? bands[i].channels : []
      return [{ value: "", label: "Auto" }].concat(channels.map(function(channel) {
        return { value: String(channel), label: "Channel " + String(channel) }
      }))
    }
  }
  return [{ value: "", label: "Auto" }]
}

function findAdapter(adapters, iface) {
  var values = Array.isArray(adapters) ? adapters : []
  for (var i = 0; i < values.length; i++) {
    if (asString(values[i].iface) === asString(iface)) return values[i]
  }
  return values.length ? values[0] : null
}

function defaultBand(adapter) {
  var options = bandOptions(adapter)
  if (!options.length) return ""
  for (var i = 0; i < options.length; i++) {
    if (options[i].value === "bg") return "bg"
  }
  return options[0].value
}

function defaultChannel(adapter, band) {
  var options = channelsFor(adapter, band)
  return options.length > 1 ? options[1].value : ""
}

function validateMac(value) {
  var mac = asString(value).trim()
  if (mac === "") return ""
  if (!/^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/i.test(mac)) return "BSSID must look like 02:11:22:33:44:55"
  var first = parseInt(mac.substring(0, 2), 16)
  if ((first & 1) !== 0) return "BSSID must be a unicast MAC address"
  return ""
}

function validateSsid(value) {
  var ssid = asString(value)
  if (ssid.length === 0) return "SSID cannot be empty"
  if (ssid.indexOf("\n") !== -1 || ssid.indexOf("\r") !== -1) return "SSID cannot contain line breaks"
  if (ssid.length > 32) return "SSID must be at most 32 characters"
  return ""
}

function validatePassword(value, security) {
  if (security === "open") return ""
  var password = asString(value)
  if (password.length < 8) return "Password must contain at least 8 characters"
  if (password.length > 63) return "Password must be at most 63 characters"
  if (password.indexOf("\n") !== -1 || password.indexOf("\r") !== -1) return "Password cannot contain line breaks"
  return ""
}

function validateSettings(settings, adapter) {
  var value = settings || {}
  var error = validateSsid(value.ssid)
  if (error) return error
  error = validateMac(value.bssid)
  if (error) return error
  error = validatePassword(value.password, value.security)
  if (error) return error
  if (!adapter) return "No Wi-Fi adapter was detected"
  if (!adapter.apSupported) return "This adapter does not advertise AP mode support"
  if (value.band && !bandOptions(adapter).some(function(option) { return option.value === value.band })) {
    return "Selected band is not reported by this adapter"
  }
  if (value.channel) {
    var channels = channelsFor(adapter, value.band).map(function(option) { return option.value })
    if (channels.indexOf(String(value.channel)) === -1) return "Selected channel is not reported for this band"
  }
  return ""
}

function formatRemaining(seconds) {
  var value = Math.max(0, Math.floor(Number(seconds) || 0))
  var hours = Math.floor(value / 3600)
  var minutes = Math.floor((value % 3600) / 60)
  var secs = value % 60
  if (hours > 0) return hours + "h " + String(minutes).padStart(2, "0") + "m"
  return minutes + "m " + String(secs).padStart(2, "0") + "s"
}

function capabilitySummary(adapter) {
  if (!adapter) return "No Wi-Fi adapter detected"
  var bands = Array.isArray(adapter.bands) ? adapter.bands.map(function(band) { return band.label }).join(" · ") : "none"
  var ap = adapter.apSupported ? "AP supported" : "AP unavailable"
  var concurrent = adapter.concurrent ? "STA+AP supported" : "STA+AP not reported"
  return ap + " · " + concurrent + " · " + bands
}

function statusLabel(status) {
  var value = status || {}
  if (value.active) return "Active"
  if (value.busy) return "Working…"
  return "Off"
}

function parseReplyLine(line) {
  try {
    return JSON.parse(String(line || ""))
  } catch (error) {
    return { id: "", ok: false, error: "Backend returned invalid JSON" }
  }
}

var exports = {
  adapterOptions: adapterOptions,
  bandOptions: bandOptions,
  channelsFor: channelsFor,
  findAdapter: findAdapter,
  defaultBand: defaultBand,
  defaultChannel: defaultChannel,
  validateMac: validateMac,
  validateSsid: validateSsid,
  validatePassword: validatePassword,
  validateSettings: validateSettings,
  formatRemaining: formatRemaining,
  capabilitySummary: capabilitySummary,
  statusLabel: statusLabel,
  parseReplyLine: parseReplyLine
}

if (typeof module !== "undefined") module.exports = exports
