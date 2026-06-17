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

  function closeInlineEdits(exceptTarget) {
    document.querySelectorAll(".inline-edit-row").forEach(function (editRow) {
      if (exceptTarget && editRow === exceptTarget) {
        return;
      }
      editRow.hidden = true;
    });
    document.querySelectorAll("[data-ledger-row]").forEach(function (row) {
      if (exceptTarget && row.nextElementSibling === exceptTarget) {
        return;
      }
      row.classList.remove("is-editing");
    });
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
        if (confirmForm.getAttribute("data-confirmed") === "true") {
          return;
        }
        event.preventDefault();
        var message = confirmForm.getAttribute("data-confirm-message") || "Are you sure?";
        var row = confirmForm.closest("tr");
        if (row) {
          closeInlineEdits();
          row.classList.add("is-deleting");
        }
        window.setTimeout(function () {
          if (window.confirm(message)) {
            confirmForm.setAttribute("data-confirmed", "true");
            confirmForm.submit();
          } else if (row) {
            row.classList.remove("is-deleting");
          }
        }, 30);
      });
    });

    document.querySelectorAll("[data-inline-edit-toggle]").forEach(function (button) {
      button.addEventListener("click", function () {
        var targetId = button.getAttribute("data-target");
        if (!targetId) {
          return;
        }
        var target = document.getElementById(targetId);
        if (!target) {
          return;
        }
        var row = button.closest("tr");
        var shouldOpen = target.hidden;
        closeInlineEdits(target);
        if (shouldOpen) {
          target.hidden = false;
          if (row) {
            row.classList.add("is-editing");
          }
          var firstInput = target.querySelector("input:not([type='hidden']), select, textarea");
          if (firstInput) {
            firstInput.focus();
          }
        } else {
          target.hidden = true;
          if (row) {
            row.classList.remove("is-editing");
          }
        }
      });
    });

    document.querySelectorAll("[data-inline-edit-cancel]").forEach(function (button) {
      button.addEventListener("click", function () {
        var editRow = button.closest(".inline-edit-row");
        if (editRow) {
          editRow.hidden = true;
          var row = editRow.previousElementSibling;
          if (row) {
            row.classList.remove("is-editing");
          }
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
