(function () {
  function numberValue(form, name) {
    var input = controlFor(form, name);
    if (!input) {
      return 0;
    }
    var value = parseFloat(String(input.value || "").replace(/[$,]/g, ""));
    return Number.isFinite(value) ? value : 0;
  }

  function setMoney(form, name, value) {
    var input = controlFor(form, name);
    if (input && Number.isFinite(value)) {
      input.value = value.toFixed(2);
    }
  }

  function formatMoney(value) {
    var amount = Number.isFinite(value) ? value : 0;
    var prefix = amount < 0 ? "-$" : "$";
    return prefix + Math.abs(amount).toFixed(2);
  }

  function setControlValue(form, name, value) {
    if (value === undefined || value === null) {
      return;
    }
    var input = controlFor(form, name);
    if (!input || input.type === "file") {
      return;
    }
    if (name === "paystub_sent") {
      input.value = String(value).toUpperCase() === "Y" || String(value).toLowerCase() === "yes" ? "Yes" : "No";
      return;
    }
    input.value = String(value);
  }

  function controlFor(form, name) {
    var selector = '[name="' + name + '"]';
    return form.querySelector(selector) || (form.id ? document.querySelector('[form="' + form.id + '"]' + selector) : null);
  }

  function updatePayroll(form) {
    var rate = numberValue(form, "vendor_pay");
    var hours = numberValue(form, "hours");
    var gross = numberValue(form, "gross");
    if (rate && hours) {
      gross = rate * hours;
    }
    if (!gross) {
      return;
    }
    var tax = numberValue(form, "tax");
    var employeePay = numberValue(form, "employee_pay") || gross - tax;
    setMoney(form, "gross", gross);
    setMoney(form, "employee_pay", employeePay);
  }

  function ensurePayrollDefaults(form) {
    var pctInput = controlFor(form, "pct");
    if (pctInput && !pctInput.value) {
      pctInput.value = "0";
    }
  }

  function initPayrollCalculator(form) {
    ensurePayrollDefaults(form);
    ["vendor_pay", "hours", "gross", "tax"].forEach(function (name) {
      var input = controlFor(form, name);
      if (input) {
        input.addEventListener("input", function () {
          updatePayroll(form);
        });
      }
    });
    updatePayroll(form);
  }

  function updateBankSigned(form) {
    var amount = numberValue(form, "amount");
    var typeInput = controlFor(form, "type");
    var signedTarget = form.id ? document.querySelector('[form="' + form.id + '"] ~ [data-bank-inline-signed]') : null;
    if (!signedTarget) {
      var row = form.closest("tr") || (form.id ? document.querySelector('[data-inline-create-row]:has([form="' + form.id + '"])') : null);
      signedTarget = row ? row.querySelector("[data-bank-inline-signed]") : null;
    }
    if (!signedTarget) {
      return;
    }
    var negativeTypes = { Expense: true, Withdrawal: true, "Transfer Out": true, "Adjustment Out": true };
    var type = typeInput ? typeInput.value : "";
    var signed = negativeTypes[type] ? -Math.abs(amount) : Math.abs(amount);
    signedTarget.textContent = amount ? formatMoney(signed) : "--";
    signedTarget.classList.toggle("negative", signed < 0);
    signedTarget.classList.toggle("positive", signed > 0);
  }

  function initBankForm(form) {
    ["type", "amount"].forEach(function (name) {
      var input = controlFor(form, name);
      if (input) {
        input.addEventListener("input", function () {
          updateBankSigned(form);
        });
        input.addEventListener("change", function () {
          updateBankSigned(form);
        });
      }
    });
    updateBankSigned(form);
  }

  function parseLocalDate(value) {
    var parts = String(value || "").split("-");
    if (parts.length !== 3) {
      return null;
    }
    var year = parseInt(parts[0], 10);
    var month = parseInt(parts[1], 10) - 1;
    var day = parseInt(parts[2], 10);
    if (!year || month < 0 || !day) {
      return null;
    }
    return new Date(year, month, day);
  }

  function updateInvoiceDueStatus(form) {
    var statusInput = controlFor(form, "status");
    var dueInput = controlFor(form, "due_date");
    var amountInput = controlFor(form, "amount");
    var balanceInput = controlFor(form, "balance_due");
    var commissionPctInput = controlFor(form, "commission_pct");
    var commissionAmountInput = controlFor(form, "commission_amount");
    var row = form.closest("tr");
    var badge = row ? row.querySelector("[data-invoice-inline-due-status]") : null;
    var commissionTarget = row ? row.querySelector("[data-invoice-inline-commission]") : null;
    var status = statusInput ? statusInput.value : "Not Received";
    var amount = numberValue(form, "amount");
    var commissionPct = numberValue(form, "commission_pct") || 30;
    var commissionFraction = commissionPct <= 1 ? commissionPct : commissionPct / 100;
    var calculatedCommission = amount * commissionFraction;
    if (commissionAmountInput && !commissionAmountInput.value && amount) {
      commissionAmountInput.value = calculatedCommission.toFixed(2);
    }
    if (commissionTarget) {
      commissionTarget.textContent = amount ? formatMoney(calculatedCommission) : "--";
    }
    if (balanceInput && amountInput && !balanceInput.value && amountInput.value) {
      balanceInput.value = status === "Received" || status === "Void" ? "0.00" : numberValue(form, "amount").toFixed(2);
    }
    if (!badge) {
      return;
    }
    badge.classList.remove("paid", "overdue", "due");
    if (status === "Received") {
      badge.textContent = "Paid";
      badge.classList.add("paid");
      return;
    }
    if (status === "Void") {
      badge.textContent = "Void";
      badge.classList.add("paid");
      return;
    }
    var dueDate = parseLocalDate(dueInput ? dueInput.value : "");
    if (!dueDate) {
      badge.textContent = "Draft";
      badge.classList.add("due");
      return;
    }
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var days = Math.round((dueDate.getTime() - today.getTime()) / 86400000);
    if (days < 0) {
      badge.textContent = "Overdue";
      badge.classList.add("overdue");
    } else if (days === 0) {
      badge.textContent = "Due today";
      badge.classList.add("due");
    } else {
      badge.textContent = "Due in " + days + "d";
      badge.classList.add("due");
    }
  }

  function initInvoiceForm(form) {
    ["status", "due_date", "amount", "commission_pct"].forEach(function (name) {
      var input = controlFor(form, name);
      if (input) {
        input.addEventListener("input", function () {
          updateInvoiceDueStatus(form);
        });
        input.addEventListener("change", function () {
          updateInvoiceDueStatus(form);
        });
      }
    });
    updateInvoiceDueStatus(form);
  }

  function rowEditors(row) {
    return Array.prototype.slice.call(row.querySelectorAll(".cell-editor"));
  }

  function setActionState(row, editing) {
    var editButton = row.querySelector("[data-inline-edit-toggle]");
    var saveButton = row.querySelector(".inline-save-button");
    var cancelButton = row.querySelector(".inline-cancel-button");
    var deleteForm = row.querySelector("[data-inline-delete-form]");
    if (editButton) {
      editButton.hidden = editing;
    }
    if (saveButton) {
      saveButton.hidden = !editing;
    }
    if (cancelButton) {
      cancelButton.hidden = !editing;
    }
    if (deleteForm) {
      deleteForm.hidden = editing;
    }
  }

  function resetRowEditors(row) {
    rowEditors(row).forEach(function (editor) {
      if (editor.getAttribute("data-original") !== null) {
        editor.value = editor.getAttribute("data-original");
      }
    });
  }

  function setRowEditing(row, editing, focusEditor) {
    if (!row) {
      return;
    }
    row.classList.toggle("is-editing", editing);
    rowEditors(row).forEach(function (editor) {
      editor.disabled = !editing;
    });
    setActionState(row, editing);
    if (editing && focusEditor) {
      focusEditor.focus();
      if (typeof focusEditor.select === "function" && focusEditor.tagName !== "SELECT") {
        focusEditor.select();
      }
    }
  }

  function closeInlineEdits(exceptRow) {
    document.querySelectorAll("[data-inline-edit-row]").forEach(function (row) {
      if (exceptRow && row === exceptRow) {
        return;
      }
      resetRowEditors(row);
      setRowEditing(row, false);
    });
  }

  function inlineStatusFor(fileInput) {
    var cell = fileInput.closest(".inline-file-cell");
    return cell ? cell.querySelector("[data-inline-file-status], [data-payroll-inline-status]") : null;
  }

  function setInlineFileStatus(fileInput, message, state) {
    var status = inlineStatusFor(fileInput);
    if (!status) {
      return;
    }
    status.textContent = message || "";
    status.classList.toggle("is-error", state === "error");
    status.classList.toggle("is-success", state === "success");
    status.classList.toggle("is-reading", state === "reading");
  }

  function fieldNamesForKind(kind) {
    var fields = {
      bank: ["date", "type", "category", "detail", "source", "amount", "notes", "attachment_path"],
      expense: ["date", "category", "vendor", "description", "amount", "paid_by", "frequency", "notes", "attachment_path"],
      invoice: ["date", "invoice_number", "customer", "status", "due_date", "amount", "commission_pct", "commission_amount", "balance_due", "source_pdf"],
      payroll: [
        "month",
        "first_name",
        "last_name",
        "vendor",
        "client",
        "job_start",
        "job_end",
        "vendor_pay",
        "pct",
        "hours",
        "gross",
        "tax",
        "commission",
        "employee_pay",
        "credit_date",
        "attachment_path",
        "paystub_sent",
      ],
    };
    return fields[kind] || [];
  }

  function fillFromExtract(form, payload, kind) {
    fieldNamesForKind(kind).forEach(function (name) {
      setControlValue(form, name, payload[name]);
    });
    if (kind === "payroll") {
      ensurePayrollDefaults(form);
      updatePayroll(form);
    } else if (kind === "bank") {
      updateBankSigned(form);
    } else if (kind === "invoice") {
      updateInvoiceDueStatus(form);
    }
  }

  function readInlineExtractFile(fileInput) {
    if (!fileInput.files || !fileInput.files.length) {
      setInlineFileStatus(fileInput, "", "");
      return;
    }
    var form = fileInput.form || (fileInput.getAttribute("form") ? document.getElementById(fileInput.getAttribute("form")) : null);
    if (!form) {
      return;
    }
    var row = fileInput.closest("tr");
    var file = fileInput.files[0];
    var kind = fileInput.getAttribute("data-extract-kind") || "payroll";
    var url = fileInput.getAttribute("data-extract-url") || "/payroll/extract-inline";
    var payload = new FormData();
    payload.append("attachment", file);
    fileInput.disabled = true;
    if (row) {
      row.classList.add("is-reading");
    }
    setInlineFileStatus(fileInput, "Reading " + file.name + "...", "reading");

    fetch(url, {
      method: "POST",
      body: payload,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!response.ok) {
              throw new Error(data.error || "Could not read the uploaded file");
            }
            return data;
          });
      })
      .then(function (data) {
        fillFromExtract(form, data, kind);
        fileInput.value = "";
        setInlineFileStatus(fileInput, "Filled from " + (data.source_name || file.name), "success");
      })
      .catch(function (error) {
        setInlineFileStatus(fileInput, error.message || "Could not read this file", "error");
      })
      .finally(function () {
        fileInput.disabled = false;
        if (row) {
          row.classList.remove("is-reading");
        }
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

    document.querySelectorAll("[data-inline-create-toggle]").forEach(function (button) {
      var targetId = button.getAttribute("aria-controls");
      var row = targetId ? document.getElementById(targetId) : null;
      if (!row) {
        return;
      }
      var form = row.querySelector("[data-inline-create-form]");
      var cancelButton = row.querySelector("[data-inline-create-cancel]");

      function openRow() {
        closeInlineEdits();
        row.hidden = false;
        button.setAttribute("aria-expanded", "true");
        window.requestAnimationFrame(function () {
          row.classList.add("is-creating");
          var firstField = row.querySelector(".table-create-input");
          if (firstField) {
            firstField.focus();
          }
        });
      }

      function closeRow(resetForm) {
        row.classList.remove("is-creating");
        button.setAttribute("aria-expanded", "false");
        row.hidden = true;
        if (resetForm && form) {
          form.reset();
          ensurePayrollDefaults(form);
          updatePayroll(form);
          updateBankSigned(form);
          updateInvoiceDueStatus(form);
          row.querySelectorAll("[data-inline-file-status], [data-payroll-inline-status]").forEach(function (status) {
            status.textContent = "";
            status.classList.remove("is-error", "is-success", "is-reading");
          });
        }
      }

      button.addEventListener("click", function () {
        if (row.hidden) {
          openRow();
        } else {
          closeRow(true);
        }
      });

      if (cancelButton) {
        cancelButton.addEventListener("click", function () {
          closeRow(true);
        });
      }
    });

    document.querySelectorAll("[data-inline-extract-file]").forEach(function (fileInput) {
      fileInput.addEventListener("change", function () {
        readInlineExtractFile(fileInput);
      });
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
        var row = button.closest("tr");
        if (!row) {
          return;
        }
        var firstInput = row.querySelector(".cell-editor");
        closeInlineEdits(row);
        setRowEditing(row, true, firstInput);
      });
    });

    document.querySelectorAll("[data-inline-edit-cancel]").forEach(function (button) {
      button.addEventListener("click", function () {
        var row = button.closest("tr");
        if (row) {
          resetRowEditors(row);
          setRowEditing(row, false);
        }
      });
    });

    document.querySelectorAll(".cell-view").forEach(function (view) {
      view.addEventListener("click", function () {
        var row = view.closest("tr");
        if (!row) {
          return;
        }
        var editor = view.parentElement ? view.parentElement.querySelector(".cell-editor") : null;
        if (!editor) {
          return;
        }
        closeInlineEdits(row);
        setRowEditing(row, true, editor);
      });
    });

    document.querySelectorAll("[data-payroll-form]").forEach(initPayrollCalculator);
    document.querySelectorAll("[data-bank-form]").forEach(initBankForm);
    document.querySelectorAll("[data-invoice-form]").forEach(initInvoiceForm);
  });
})();
