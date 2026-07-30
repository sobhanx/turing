/**
 * Inline speaker rename on Speech Center transcript viewer.
 * PATCH /api/turing/v1/speakers/{id}/ — same contract as SpeakerViewSet.
 */
(function () {
  "use strict";

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function qsa(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function getCookie(name) {
    var match = document.cookie.match(
      new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)")
    );
    return match ? decodeURIComponent(match[1]) : "";
  }

  function resolvedName(speakerName, speakerLabel) {
    var name = String(speakerName || "").trim();
    return name || String(speakerLabel || "").trim() || "—";
  }

  function boot() {
    var cfgEl = qs("#sc-speaker-edit-config");
    if (!cfgEl) return;
    var config = {};
    try {
      config = JSON.parse(cfgEl.textContent || "{}");
    } catch (_e) {
      return;
    }
    if (!config.canEdit) return;

    var apiBase = config.speakersApiBase || "/api/turing/v1/speakers/";
    var csrfToken = config.csrfToken || getCookie("csrftoken");
    var toastEl = qs("#sc-speaker-toast");
    var viewer = qs("#sc-transcript-viewer");
    if (!viewer) return;

    var activeEdit = null;
    var toastTimer = null;

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

    function chipsForSpeaker(speakerId) {
      return qsa('[data-speaker-id="' + speakerId + '"]', viewer);
    }

    function setChipDisplay(chips, displayName, speakerName) {
      chips.forEach(function (chip) {
        chip.dataset.speakerName = speakerName;
        var textEl = qs(".sc-speaker-chip-text", chip);
        if (textEl) textEl.textContent = displayName;
      });
    }

    function setSaving(chip, saving) {
      if (!chip) return;
      chip.classList.toggle("is-saving", !!saving);
      chip.disabled = !!saving;
      var savingEl = qs(".sc-speaker-chip-saving", chip);
      if (savingEl) savingEl.hidden = !saving;
    }

    function finishEdit(restoreChip) {
      if (!activeEdit) return;
      var input = activeEdit.input;
      var chip = activeEdit.chip;
      if (input && input.parentNode) {
        input.parentNode.removeChild(input);
      }
      if (restoreChip) chip.hidden = false;
      activeEdit = null;
    }

    function saveRename(chip, speakerId, newName, previousName, previousResolved) {
      var chips = chipsForSpeaker(speakerId);
      var label = chip.dataset.speakerLabel || "";
      var optimistic = resolvedName(newName, label);

      setChipDisplay(chips, optimistic, newName);
      chips.forEach(function (c) {
        setSaving(c, true);
      });

      fetch(apiBase + encodeURIComponent(speakerId) + "/", {
        method: "PATCH",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ speaker_name: newName }),
      })
        .then(function (resp) {
          if (!resp.ok) {
            return resp.json().catch(function () {
              return { detail: "Could not rename speaker." };
            }).then(function (body) {
              throw new Error(body.detail || "Could not rename speaker.");
            });
          }
          return resp.json();
        })
        .then(function (data) {
          var savedName = data.speaker_name != null ? data.speaker_name : newName;
          var display =
            data.resolved_name || resolvedName(savedName, data.speaker_label || label);
          setChipDisplay(chips, display, savedName);
          showToast("Speaker renamed to " + display, "success");
        })
        .catch(function (err) {
          setChipDisplay(chips, previousResolved, previousName);
          showToast((err && err.message) || "Could not rename speaker.", "error");
        })
        .finally(function () {
          chips.forEach(function (c) {
            setSaving(c, false);
          });
        });
    }

    function startEdit(chip) {
      if (activeEdit) {
        finishEdit(true);
      }
      var speakerId = chip.dataset.speakerId;
      if (!speakerId) return;

      var label = chip.dataset.speakerLabel || "";
      var speakerName = chip.dataset.speakerName || "";
      var previousResolved = resolvedName(speakerName, label);
      var textEl = qs(".sc-speaker-chip-text", chip);

      chip.hidden = true;
      var input = document.createElement("input");
      input.type = "text";
      input.className = "sc-speaker-chip-input ltr";
      input.value = speakerName || label;
      input.setAttribute("aria-label", "Speaker name");
      input.maxLength = 128;
      chip.parentNode.insertBefore(input, chip.nextSibling);
      input.focus();
      input.select();

      var committed = false;

      function commit() {
        if (committed) return;
        committed = true;
        var newName = input.value.trim();
        finishEdit(true);
        if (newName === (speakerName || "").trim()) {
          return;
        }
        saveRename(chip, speakerId, newName, speakerName, previousResolved);
      }

      function cancel() {
        if (committed) return;
        committed = true;
        finishEdit(true);
      }

      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          commit();
        } else if (ev.key === "Escape") {
          ev.preventDefault();
          cancel();
        }
      });

      input.addEventListener("blur", function () {
        if (!committed) commit();
      });

      activeEdit = { chip: chip, input: input };
    }

    viewer.addEventListener("click", function (ev) {
      var chip = ev.target.closest(".sc-speaker-chip-editable");
      if (!chip || chip.disabled) return;
      ev.preventDefault();
      startEdit(chip);
    });

    viewer.addEventListener("keydown", function (ev) {
      var chip = ev.target.closest(".sc-speaker-chip-editable");
      if (!chip) return;
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        startEdit(chip);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
