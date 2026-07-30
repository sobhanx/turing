/**
 * Full-text transcript editing on Speech Center transcript viewer.
 * PATCH /api/turing/v1/transcripts/{id}/edit-body/
 */
(function () {
  "use strict";

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function getCookie(name) {
    var match = document.cookie.match(
      new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)")
    );
    return match ? decodeURIComponent(match[1]) : "";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function linebreaksHtml(value) {
    return escapeHtml(value).replace(/\r\n|\r|\n/g, "<br>");
  }

  function boot() {
    var cfgEl = qs("#sc-transcript-edit-config");
    if (!cfgEl) return;

    var config = {};
    try {
      config = JSON.parse(cfgEl.textContent || "{}");
    } catch (_e) {
      return;
    }
    if (!config.canEdit) return;

    var editBtn = qs("#sc-transcript-edit-btn");
    var readPanel = qs("#sc-transcript-read");
    var editPanel = qs("#sc-transcript-edit");
    var textarea = qs("#sc-transcript-textarea");
    var saveBtn = qs("#sc-transcript-save");
    var cancelBtn = qs("#sc-transcript-cancel");
    var toastEl = qs("#sc-speaker-toast");
    if (!editBtn || !readPanel || !editPanel || !textarea || !saveBtn || !cancelBtn) {
      return;
    }

    var editBodyUrl = config.editBodyUrl || "";
    var csrfToken = config.csrfToken || getCookie("csrftoken");
    var baselineBody = textarea.value;
    var isEditing = false;
    var isSaving = false;
    var toastTimer = null;
    var leaveMessage = "You have unsaved transcript changes. Leave anyway?";

    function isDirty() {
      return isEditing && textarea.value !== baselineBody;
    }

    function showToast(message, tone) {
      if (!toastEl || !message) return;
      toastEl.textContent = message;
      toastEl.dataset.tone = tone || "info";
      toastEl.hidden = false;
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(function () {
        toastEl.hidden = true;
        toastEl.textContent = "";
      }, 3200);
    }

    function setSavingState(saving) {
      isSaving = !!saving;
      saveBtn.disabled = saving;
      cancelBtn.disabled = saving;
      editBtn.disabled = saving;
      textarea.readOnly = saving;
      saveBtn.classList.toggle("is-saving", saving);
      var spinner = qs(".sc-transcript-save-spinner", saveBtn);
      var label = qs(".sc-transcript-save-label", saveBtn);
      if (spinner) spinner.hidden = !saving;
      if (label) label.hidden = saving;
    }

    function applyReadOnlyMode(options) {
      var opts = options || {};
      isEditing = false;
      readPanel.hidden = false;
      editPanel.hidden = true;
      editBtn.hidden = false;
      if (opts.focusEdit) {
        editBtn.focus();
      }
    }

    function enterEditMode() {
      if (isEditing || isSaving) return;
      isEditing = true;
      baselineBody = textarea.value;
      readPanel.hidden = true;
      editPanel.hidden = false;
      editBtn.hidden = true;
      textarea.focus();
    }

    function leaveEditMode(options) {
      if (!isEditing) {
        applyReadOnlyMode(options);
        return;
      }
      applyReadOnlyMode(options);
    }

    // Page always starts read-only; refresh never leaves the editor open.
    applyReadOnlyMode();

    function cancelEdit() {
      if (isSaving) return;
      textarea.value = baselineBody;
      leaveEditMode({ focusEdit: true });
    }

    function confirmDiscard() {
      if (!isDirty()) return true;
      return window.confirm(leaveMessage);
    }

    function updateReadView(data) {
      var segments = data && data.segments;
      var timeline = qs("#sc-transcript-timeline");
      if (timeline && segments && segments.length) {
        var items = timeline.querySelectorAll(".sc-timeline-item");
        segments.forEach(function (seg, index) {
          var item = items[index];
          if (!item) return;
          var timeEl = qs(".sc-timeline-time", item);
          var textEl = qs(".sc-timeline-text", item);
          if (timeEl) timeEl.textContent = seg.start_display || "";
          if (textEl) textEl.textContent = seg.text || "";
        });
        return;
      }

      var fulltext = qs("#sc-transcript-fulltext");
      if (fulltext && data && data.full_text != null) {
        fulltext.innerHTML = linebreaksHtml(data.full_text);
      }
    }

    function saveEdit() {
      if (!isEditing || isSaving) return;

      setSavingState(true);
      fetch(editBodyUrl, {
        method: "PATCH",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ body: textarea.value }),
      })
        .then(function (resp) {
          if (!resp.ok) {
            return resp.json().catch(function () {
              return { detail: "Could not save transcript." };
            }).then(function (body) {
              throw new Error(body.detail || "Could not save transcript.");
            });
          }
          return resp.json();
        })
        .then(function (data) {
          if (data.edit_body != null) {
            textarea.value = data.edit_body;
          }
          baselineBody = textarea.value;
          updateReadView(data);
          leaveEditMode({ focusEdit: true });
          showToast("Transcript saved.", "success");
        })
        .catch(function (err) {
          showToast((err && err.message) || "Could not save transcript.", "error");
        })
        .finally(function () {
          setSavingState(false);
        });
    }

    editBtn.addEventListener("click", function () {
      enterEditMode();
    });

    saveBtn.addEventListener("click", function () {
      saveEdit();
    });

    cancelBtn.addEventListener("click", function () {
      if (!confirmDiscard()) return;
      cancelEdit();
    });

    textarea.addEventListener("keydown", function (ev) {
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "s") {
        ev.preventDefault();
        saveEdit();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        if (!confirmDiscard()) return;
        cancelEdit();
      }
    });

    window.addEventListener("beforeunload", function (ev) {
      if (!isDirty()) return;
      ev.preventDefault();
      ev.returnValue = leaveMessage;
      return leaveMessage;
    });

    document.addEventListener("click", function (ev) {
      if (!isDirty()) return;
      var link = ev.target.closest("a[href]");
      if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
      if (!confirmDiscard()) {
        ev.preventDefault();
      }
    }, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
