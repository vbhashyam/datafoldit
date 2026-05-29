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
    document.querySelectorAll("[data-auto-upload-form]").forEach(function (uploadForm) {
      var fileInput = uploadForm.querySelector("[data-auto-submit-file]");
      var submitButton = uploadForm.querySelector('button[type="submit"]');
      function markSubmitting() {
        if (submitButton) {
          submitButton.disabled = true;
          var count = fileInput && fileInput.files ? fileInput.files.length : 1;
          submitButton.textContent = count > 1 ? "Reading invoices..." : "Reading invoice...";
        }
      }
      uploadForm.addEventListener("submit", markSubmitting);
      if (fileInput) {
        fileInput.addEventListener("change", function () {
          if (fileInput.files && fileInput.files.length > 0) {
            markSubmitting();
            window.setTimeout(function () {
              uploadForm.submit();
            }, 0);
          }
        });
      }
    });

    document.querySelectorAll("[data-inline-status-form]").forEach(function (statusForm) {
      var statusSelect = statusForm.querySelector('select[name="status"]');
      if (statusSelect) {
        statusSelect.addEventListener("change", function () {
          statusSelect.disabled = true;
          statusForm.submit();
        });
      }
    });

    document.querySelectorAll("[data-confirm-message]").forEach(function (confirmForm) {
      confirmForm.addEventListener("submit", function (event) {
        var message = confirmForm.getAttribute("data-confirm-message") || "Are you sure?";
        if (!window.confirm(message)) {
          event.preventDefault();
        }
      });
    });

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
