// Demo scenario buttons: fill the form with simulated values (no network calls).
(function () {
  "use strict";
  var form = document.getElementById("reading-form");
  var note = document.getElementById("scenario-note");
  if (!form) { return; }

  var expected = {
    normal: "Expected result: Normal",
    warning: "Expected result: Warning",
    leak: "Expected result: Leak suspected",
    critical: "Expected result: Critical"
  };

  document.querySelectorAll("[data-scenario]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      form.elements.pressure.value = btn.dataset.pressure;
      form.elements.flow_in.value = btn.dataset.flowIn;
      form.elements.flow_out.value = btn.dataset.flowOut;
      form.elements.temperature.value = btn.dataset.temperature;
      form.elements.sensor_status.value = "OK";
      if (note) { note.textContent = expected[btn.dataset.scenario] || ""; }
    });
  });
})();
