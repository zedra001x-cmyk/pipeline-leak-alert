// Device admin page: lists devices, registers new ones, enables/disables them.
(function () {
  "use strict";
  var rows = document.getElementById("device-rows");
  if (!rows) { return; }
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : "";
  var errEl = document.getElementById("device-error");
  var keyBox = document.getElementById("device-key");
  var select = document.getElementById("device-section");
  var sectionNames = {};

  function api(method, url, body) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { throw new Error(typeof d.detail === "string" ? d.detail : "Request failed (" + r.status + ")"); }
        return d;
      });
    });
  }

  function cell(tr, text) { var td = document.createElement("td"); td.textContent = text; tr.appendChild(td); return td; }

  function render(list) {
    while (rows.firstChild) { rows.removeChild(rows.firstChild); }
    if (!list.length) { var tr0 = document.createElement("tr"); cell(tr0, "No devices registered yet."); rows.appendChild(tr0); }
    list.forEach(function (d) {
      var tr = document.createElement("tr");
      cell(tr, d.name);
      cell(tr, sectionNames[d.pipeline_section_id] || d.pipeline_section_id);
      cell(tr, d.last_seen ? d.last_seen.replace("T", " ").slice(0, 19) : "never");
      cell(tr, d.enabled ? "Enabled" : "Disabled");
      var td = document.createElement("td");
      var b = document.createElement("button");
      b.type = "button"; b.className = "btn small"; b.textContent = d.enabled ? "Disable" : "Enable";
      b.addEventListener("click", function () {
        api("POST", "/api/devices/" + d.id + "/enabled", { enabled: !d.enabled }).then(load).catch(showError);
      });
      td.appendChild(b); tr.appendChild(td); rows.appendChild(tr);
    });
  }

  function showError(e) { errEl.textContent = e.message; }
  function load() { errEl.textContent = ""; return api("GET", "/api/devices").then(render).catch(showError); }

  document.getElementById("device-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var name = ev.target.elements.name.value.trim();
    api("POST", "/api/devices", { name: name, pipeline_section_id: parseInt(select.value, 10) }).then(function (d) {
      keyBox.hidden = false;
      keyBox.textContent = "API key for " + d.name + " (copy it now, it is shown only once): " + d.api_key;
      ev.target.reset();
      return load();
    }).catch(showError);
  });

  api("GET", "/api/pipeline-sections").then(function (secs) {
    secs.forEach(function (s) {
      sectionNames[s.id] = s.name;
      var o = document.createElement("option"); o.value = s.id; o.textContent = s.name; select.appendChild(o);
    });
    return load();
  }).catch(showError);
})();
