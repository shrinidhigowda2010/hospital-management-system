(function () {
  var doctor = document.querySelector("[data-slot-doctor]");
  var day = document.querySelector("[data-slot-date]");
  var time = document.querySelector("[data-slot-time]");
  if (!doctor || !day || !time) return;

  var endpoint = time.getAttribute("data-slots-url");
  var apptId = time.getAttribute("data-appt-id") || "";

  function fill(slots, message) {
    var keep = time.value;
    time.innerHTML = "";
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = slots.length ? "Choose a free slot" : (message || "No free slots that day");
    time.appendChild(placeholder);
    slots.forEach(function (slot) {
      var option = document.createElement("option");
      option.value = slot;
      option.textContent = slot;
      if (slot === keep) option.selected = true;
      time.appendChild(option);
    });
  }

  function refresh() {
    if (!doctor.value || !day.value) {
      fill([], "Choose a doctor and a date first");
      return;
    }
    var url = endpoint
      + "?doctor_id=" + encodeURIComponent(doctor.value)
      + "&date=" + encodeURIComponent(day.value)
      + "&appt_id=" + encodeURIComponent(apptId);
    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (data) { fill(data.slots || [], data.message); })
      .catch(function () { fill([], "Could not load slots"); });
  }

  doctor.addEventListener("change", refresh);
  day.addEventListener("change", refresh);
})();
