(function () {
  function renumber(tbody) {
    tbody.querySelectorAll("tr").forEach(function (row, index) {
      var label = row.querySelector("[data-row-label]");
      if (label) label.textContent = String(index + 1);
    });
  }

  document.addEventListener("click", function (event) {
    var add = event.target.closest("[data-add-row]");
    if (add) {
      var tbody = document.querySelector(add.getAttribute("data-add-row"));
      if (!tbody) return;
      var proto = tbody.querySelector("tr");
      if (!proto) return;
      var clone = proto.cloneNode(true);
      clone.querySelectorAll("input, select, textarea").forEach(function (el) {
        if (el.tagName === "SELECT") el.selectedIndex = 0;
        else el.value = "";
      });
      tbody.appendChild(clone);
      renumber(tbody);
      return;
    }
    var remove = event.target.closest("[data-remove-row]");
    if (remove) {
      var row = remove.closest("tr");
      var body = row && row.parentElement;
      if (!body) return;
      if (body.querySelectorAll("tr").length === 1) {
        row.querySelectorAll("input").forEach(function (el) { el.value = ""; });
        row.querySelectorAll("select").forEach(function (el) { el.selectedIndex = 0; });
        return;
      }
      row.remove();
      renumber(body);
    }
  });
})();
