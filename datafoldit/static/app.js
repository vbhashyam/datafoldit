(function () {
  function numberValue(form, name) {
    var input = form.querySelector('[name="' + name + '"]');
    if (!input) {
      return 0;
    }
    var value = parseFloat(String(input.value || "").replace(/[$,]/g, ""));
    return Number.isFinite(value) ? value : 0;
  }

  function setMoney(form, name, value) {
    var input = form.querySelector('[name="' + name + '"]');
    if (input && Number.isFinite(value)) {
      input.value = value.toFixed(2);
    }
  }

  function updatePayroll(form) {
    var rate = numberValue(form, "vendor_pay");
    var hours = numberValue(form, "hours");
    var pct = numberValue(form, "pct");
    if (!rate || !hours) {
      return;
    }
    var fraction = pct <= 1 ? pct : pct / 100;
    var gross = rate * hours;
    var commission = gross * fraction;
    var employeePay = gross - commission;
    setMoney(form, "gross", gross);
    setMoney(form, "commission", commission);
    setMoney(form, "employee_pay", employeePay);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.querySelector("[data-payroll-form]");
    if (!form) {
      return;
    }
    var pctInput = form.querySelector('[name="pct"]');
    if (pctInput && !pctInput.value) {
      pctInput.value = "30";
    }
    ["vendor_pay", "hours", "pct"].forEach(function (name) {
      var input = form.querySelector('[name="' + name + '"]');
      if (input) {
        input.addEventListener("input", function () {
          updatePayroll(form);
        });
      }
    });
    updatePayroll(form);
  });
})();
