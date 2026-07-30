/**
 * Wire Record Audio tab: MediaRecorder + waveform trim + existing upload path.
 */
(function (global) {
  "use strict";

  var NS = (global.TuringSpeechCenter = global.TuringSpeechCenter || {});

  function $(id) {
    return document.getElementById(id);
  }

  function initTabs() {
    var tabs = document.querySelectorAll("[data-sc-upload-tab]");
    var panels = {
      file: $("sc-panel-file"),
      record: $("sc-panel-record"),
    };
    function activate(name) {
      tabs.forEach(function (btn) {
        var on = btn.getAttribute("data-sc-upload-tab") === name;
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-selected", on ? "true" : "false");
      });
      Object.keys(panels).forEach(function (key) {
        if (!panels[key]) return;
        panels[key].hidden = key !== name;
      });
      try {
        var url = new URL(global.location.href);
        if (name === "record") url.searchParams.set("tab", "record");
        else url.searchParams.delete("tab");
        global.history.replaceState({}, "", url);
      } catch (_e) {
        /* ignore */
      }
    }
    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        activate(btn.getAttribute("data-sc-upload-tab"));
      });
    });
    var initial = "file";
    try {
      var params = new URLSearchParams(global.location.search);
      if (params.get("tab") === "record") initial = "record";
    } catch (_e2) {
      /* ignore */
    }
    activate(initial);
    return { activate: activate };
  }

  function initFileDropzone() {
    var zone = $("sc-dropzone");
    var input = $("sc-file-input");
    var nameEl = $("sc-file-name");
    var hintEl = $("sc-file-hint");
    if (!zone || !input) return;

    function formatBytes(n) {
      if (!n && n !== 0) return "";
      if (n < 1024) return n + " B";
      if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
      return (n / 1048576).toFixed(1) + " MB";
    }

    function showFile(file) {
      if (!file || !nameEl) return;
      nameEl.hidden = false;
      nameEl.textContent = file.name + (file.size ? " · " + formatBytes(file.size) : "");
      if (hintEl) hintEl.hidden = true;
    }

    input.addEventListener("change", function () {
      if (input.files && input.files[0]) showFile(input.files[0]);
    });
    zone.addEventListener("click", function (e) {
      if (e.target === input) return;
      input.click();
    });
    zone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        input.click();
      }
    });
    ["dragenter", "dragover"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.remove("is-dragover");
      });
    });
    zone.addEventListener("drop", function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      var dt = new DataTransfer();
      dt.items.add(files[0]);
      input.files = dt.files;
      showFile(files[0]);
    });
  }

  function initRecorder(config) {
    var VoiceRecorder = NS.VoiceRecorder;
    var WaveformEditor = NS.WaveformEditor;
    var RecorderUploader = NS.RecorderUploader;
    if (!VoiceRecorder || !WaveformEditor || !RecorderUploader) return;

    var root = $("sc-recorder");
    if (!root) return;

    var permEl = $("sc-rec-permission");
    var timerEl = $("sc-rec-timer");
    var durEl = $("sc-rec-duration");
    var statusEl = $("sc-rec-status");
    var errorEl = $("sc-rec-error");
    var unsupportedEl = $("sc-rec-unsupported");
    var controls = $("sc-rec-controls");
    var review = $("sc-rec-review");
    var canvas = $("sc-rec-waveform");
    var audioEl = $("sc-rec-playback");
    var trimLabel = $("sc-rec-trim-label");
    var progressEl = $("sc-rec-progress");
    var progressWrap = $("sc-rec-progress-wrap");
    var progressFill = $("sc-rec-progress-fill");
    var formatEl = $("sc-rec-format");
    var sizeEl = $("sc-rec-size");
    var orgLabel = $("sc-rec-org-label");
    var fileOrgSelect = $("sc-file-org");
    var orgSelect = $("sc-rec-org") || document.querySelector(
      '#sc-panel-record [name="organization_id"], #sc-rec-org'
    );

    var btnStart = $("sc-rec-start");
    var btnPause = $("sc-rec-pause");
    var btnResume = $("sc-rec-resume");
    var btnStop = $("sc-rec-stop");
    var btnDelete = $("sc-rec-delete");
    var btnAgain = $("sc-rec-again");
    var btnSave = $("sc-rec-save");
    var btnPlay = $("sc-rec-play-focus");
    var btnTrim = $("sc-rec-trim-focus");

    var STATUS_LABELS = {
      idle: "Ready to record",
      requesting: "Requesting microphone…",
      recording: "Recording in progress",
      paused: "Recording paused",
      stopped: "Review your recording",
      denied: "Permission denied",
      unsupported: "Unsupported",
      uploading: "Uploading",
      completed: "Recording uploaded successfully",
      failed: "Upload failed",
    };

    var STEP_FILL = {
      preparing: 18,
      uploading: 55,
      complete: 82,
      redirecting: 100,
    };

    function formatBytes(n) {
      n = Number(n) || 0;
      if (n < 1024) return n + " B";
      if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
      return (n / 1048576).toFixed(1) + " MB";
    }

    function mimeToFormat(mime) {
      mime = String(mime || "").toLowerCase();
      if (mime.indexOf("webm") >= 0) return "WebM";
      if (mime.indexOf("ogg") >= 0) return "Ogg";
      if (mime.indexOf("wav") >= 0) return "WAV";
      if (mime.indexOf("mp4") >= 0 || mime.indexOf("m4a") >= 0) return "MP4";
      return mime || "Audio";
    }

    function updateOrgLabel() {
      if (!orgLabel || !orgSelect) return;
      var opt = orgSelect.options[orgSelect.selectedIndex];
      orgLabel.textContent = opt && opt.value ? opt.textContent : "—";
    }

    function syncOrgSelects(source) {
      var value = source && source.value ? source.value : "";
      if (orgSelect && source !== orgSelect) orgSelect.value = value;
      if (fileOrgSelect && source !== fileOrgSelect) fileOrgSelect.value = value;
      updateOrgLabel();
    }

    function requireOrg() {
      if (!orgSelect || !orgSelect.value) {
        setError("Select an organization before recording or uploading.");
        if (orgSelect) orgSelect.focus();
        return false;
      }
      return true;
    }

    function setError(msg) {
      if (!errorEl) return;
      if (!msg) {
        errorEl.hidden = true;
        errorEl.textContent = "";
        return;
      }
      errorEl.hidden = false;
      errorEl.textContent = msg;
      if (statusEl) {
        statusEl.textContent = STATUS_LABELS.failed;
        statusEl.dataset.state = "failed";
      }
    }

    function setPermission(text, tone) {
      if (!permEl) return;
      permEl.textContent = text;
      permEl.dataset.tone = tone || "neutral";
    }

    function setStatus(state) {
      if (!statusEl) return;
      statusEl.textContent = STATUS_LABELS[state] || state;
      statusEl.dataset.state = state;
    }

    function setStep(step, pct) {
      if (!progressEl) return;
      if (progressWrap) progressWrap.hidden = false;
      progressEl.hidden = false;
      var items = progressEl.querySelectorAll("[data-step]");
      var order = RecorderUploader.STEPS;
      var idx = order.indexOf(step);
      items.forEach(function (li) {
        var s = li.getAttribute("data-step");
        var si = order.indexOf(s);
        li.classList.toggle("is-done", si >= 0 && si < idx);
        li.classList.toggle("is-active", s === step);
        var meta = li.querySelector(".sc-rec-step-meta");
        if (meta) {
          meta.textContent =
            s === "uploading" && typeof pct === "number" ? pct + "%" : "";
        }
      });
      if (progressFill) {
        var fill = STEP_FILL[step] || 0;
        if (step === "uploading" && typeof pct === "number") {
          fill = Math.min(78, 40 + Math.round(pct * 0.38));
        }
        progressFill.style.width = fill + "%";
      }
      if (step === "preparing" || step === "uploading" || step === "redirecting") {
        setStatus("uploading");
      } else if (step === "complete") {
        setStatus("completed");
      }
    }

    function hideProgress() {
      if (progressWrap) progressWrap.hidden = true;
      if (progressEl) progressEl.hidden = true;
      if (progressFill) progressFill.style.width = "0%";
    }

    function showSuccess(filename) {
      var el = $("sc-rec-success");
      if (!el) return;
      el.hidden = false;
      el.textContent = filename
        ? "Recording uploaded successfully: " + filename
        : "Recording uploaded successfully";
      setStatus("completed");
    }

    function devLog(payload) {
      if (!config || !config.debug) return;
      if (typeof console !== "undefined" && console.info) {
        console.info("[TuringRecorder]", payload);
      }
    }

    function assertUploadableBlob(blob, durationMs) {
      var result = VoiceRecorder.validateBlob(blob, durationMs);
      if (!result.ok) {
        setError(result.message);
        return false;
      }
      return true;
    }

    if (!VoiceRecorder.isSupported()) {
      if (unsupportedEl) unsupportedEl.hidden = false;
      if (controls) controls.hidden = true;
      setPermission("Unsupported browser", "danger");
      return;
    }

    var recorder = new VoiceRecorder({
      preferredMimeTypes: config.preferredMimeTypes,
      onStateChange: function (state) {
        root.dataset.state = state;
        setStatus(state);
        if (state === "denied") {
          setPermission("Microphone: Denied ✗", "danger");
          setError(
            "Microphone access was denied. Allow the microphone in your browser settings and try again."
          );
        } else if (state === "requesting") {
          setPermission("Microphone: Requesting…", "warn");
        } else if (state === "recording" || state === "paused" || state === "stopped") {
          setPermission("Microphone: Allowed ✓", "ok");
          if (state !== "stopped") setError("");
        } else if (state === "idle") {
          setPermission("Microphone: Ready", "ok");
        }

        var recording = state === "recording";
        var paused = state === "paused";
        var stopped = state === "stopped";
        if (btnStart) btnStart.hidden = recording || paused || stopped;
        if (btnPause) btnPause.hidden = !recording;
        if (btnResume) btnResume.hidden = !paused;
        if (btnStop) btnStop.hidden = !(recording || paused);
        if (review) review.hidden = !stopped;
        if (btnDelete) btnDelete.disabled = !(stopped || recording || paused);
      },
      onTick: function (ms, label) {
        if (!timerEl) return;
        var totalSec = Math.floor(Math.max(0, ms) / 1000);
        var h = Math.floor(totalSec / 3600);
        var m = Math.floor((totalSec % 3600) / 60);
        var s = totalSec % 60;
        function pad(n) {
          return (n < 10 ? "0" : "") + n;
        }
        timerEl.textContent = pad(h) + ":" + pad(m) + ":" + pad(s);
      },
      onError: function (err) {
        setError((err && err.message) || "Recording error");
      },
    });

    var wave = canvas
      ? new WaveformEditor(canvas, {
          onChange: function (trim) {
            if (!trimLabel) return;
            var VoiceFmt = VoiceRecorder.formatDuration;
            trimLabel.textContent =
              "Selection " +
              VoiceFmt(trim.startSec * 1000) +
              " – " +
              VoiceFmt(trim.endSec * 1000) +
              " (" +
              VoiceFmt(trim.durationSec * 1000) +
              ")";
            if (durEl) durEl.textContent = VoiceFmt(trim.durationSec * 1000);
          },
        })
      : null;

    var uploader = new RecorderUploader({
      uploadUrl: config.uploadUrl,
      redirectUrl: config.redirectUrl,
      csrfToken: config.csrfToken,
      maxUploadBytes: config.maxUploadBytes,
      uploadSource: "recorder",
      onStep: setStep,
      onProgress: function () {},
      onError: setError,
    });

    var playbackObjectUrl = null;

    function revokePlaybackUrl() {
      if (playbackObjectUrl) {
        URL.revokeObjectURL(playbackObjectUrl);
        playbackObjectUrl = null;
      }
      if (audioEl) {
        audioEl.removeAttribute("src");
        try {
          audioEl.load();
        } catch (_e) {
          /* ignore */
        }
      }
    }

    function syncPlayback(blob) {
      if (!audioEl || !blob) return;
      // Always play the original recorded blob — never a re-encoded/trim export.
      revokePlaybackUrl();
      playbackObjectUrl = URL.createObjectURL(blob);
      audioEl.src = playbackObjectUrl;
      audioEl.hidden = false;
      try {
        audioEl.load();
      } catch (_e) {
        /* ignore */
      }
    }

    function afterStop(blob) {
      // Playback source must remain the recorder's original blob.
      syncPlayback(blob || recorder.blob);
      var active = blob || recorder.blob;
      if (durEl) {
        durEl.textContent = VoiceRecorder.formatDuration(recorder.elapsedMs);
      }
      if (formatEl) formatEl.textContent = mimeToFormat(active && active.type);
      if (sizeEl) sizeEl.textContent = formatBytes(active && active.size);
      updateOrgLabel();
      if (wave) {
        wave.loadBlob(blob || recorder.blob).catch(function () {
          setError(
            "Waveform preview unavailable for this format. You can still upload the full recording."
          );
        });
      }
    }

    if (orgSelect) {
      orgSelect.addEventListener("change", function () {
        syncOrgSelects(orgSelect);
        setError("");
      });
    }
    if (fileOrgSelect) {
      fileOrgSelect.addEventListener("change", function () {
        syncOrgSelects(fileOrgSelect);
      });
    }
    updateOrgLabel();

    if (btnStart) {
      btnStart.addEventListener("click", function () {
        if (!requireOrg()) return;
        setError("");
        hideProgress();
        recorder.start().catch(function (err) {
          setError((err && err.message) || "Could not start recording.");
        });
      });
    }
    if (btnPause) btnPause.addEventListener("click", function () {
      recorder.pause();
    });
    if (btnResume) btnResume.addEventListener("click", function () {
      recorder.resume();
    });
    if (btnStop) {
      btnStop.addEventListener("click", function () {
        recorder.stop().then(function (blob) {
          var validation = VoiceRecorder.validateBlob(blob, recorder.elapsedMs);
          devLog(
            Object.assign(recorder.getDebugSnapshot(), {
              event: "stop",
              validation: validation,
            })
          );
          afterStop(blob);
          if (!validation.ok) {
            setError(validation.message);
            if (btnSave) btnSave.disabled = true;
            return;
          }
          setError("");
          if (btnSave) {
            btnSave.disabled = !!(orgSelect && orgSelect.disabled);
          }
        });
      });
    }
    if (btnDelete) {
      btnDelete.addEventListener("click", function () {
        revokePlaybackUrl();
        if (audioEl) audioEl.hidden = true;
        recorder.deleteRecording();
        if (wave) wave.reset();
        if (timerEl) timerEl.textContent = "00:00:00";
        if (durEl) durEl.textContent = "00:00";
        if (formatEl) formatEl.textContent = "—";
        if (sizeEl) sizeEl.textContent = "—";
        hideProgress();
        if (btnSave) btnSave.disabled = !!(orgSelect && orgSelect.disabled);
        setError("");
      });
    }
    if (btnAgain) {
      btnAgain.addEventListener("click", function () {
        if (!requireOrg()) return;
        revokePlaybackUrl();
        if (audioEl) audioEl.hidden = true;
        if (wave) wave.reset();
        hideProgress();
        if (btnSave) btnSave.disabled = !!(orgSelect && orgSelect.disabled);
        setError("");
        recorder.recordAgain().catch(function (err) {
          setError((err && err.message) || "Could not restart recording.");
        });
      });
    }
    if (btnPlay && audioEl) {
      btnPlay.addEventListener("click", function () {
        if (audioEl.hidden) audioEl.hidden = false;
        if (audioEl.paused) {
          audioEl.play().catch(function () {
            /* ignore */
          });
        } else {
          audioEl.pause();
        }
      });
    }
    if (btnTrim && canvas) {
      btnTrim.addEventListener("click", function () {
        canvas.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
    if (btnSave) {
      btnSave.addEventListener("click", function () {
        setError("");
        if (!requireOrg()) return;
        if (!recorder.blob) {
          setError("Nothing to upload yet.");
          return;
        }
        if (!assertUploadableBlob(recorder.blob, recorder.elapsedMs)) {
          if (btnSave) btnSave.disabled = true;
          return;
        }
        var orgId = orgSelect ? orgSelect.value : "";
        setStep("preparing", 0);
        var exportPromise = wave
          ? wave.exportBlob(recorder.blob)
          : Promise.resolve(recorder.blob);
        exportPromise
          .then(function (blob) {
            // Duration gate uses wall-clock recording time; size must still be sane.
            if (!assertUploadableBlob(blob, recorder.elapsedMs)) {
              throw new Error(
                "Recording export produced an incomplete file. Please record again."
              );
            }
            var mime = (blob && blob.type) || recorder.mimeType || "audio/webm";
            var ext =
              mime.indexOf("wav") !== -1
                ? "wav"
                : VoiceRecorder.extensionForMime(mime);
            var filename =
              "recording-" +
              new Date().toISOString().replace(/[:.]/g, "-") +
              "." +
              ext;
            var file = new File([blob], filename, {
              type: mime,
              lastModified: Date.now(),
            });
            devLog(
              Object.assign(recorder.getDebugSnapshot(filename), {
                event: "upload",
                uploadBytes: file.size,
                selectedMime: mime,
              })
            );
            return uploader.uploadFile(file, orgId);
          })
          .then(function (result) {
            showSuccess(result.filename);
            setStep("complete", 100);
            setStep("redirecting", 100);
            var target = result.redirectUrl || config.redirectUrl;
            global.setTimeout(function () {
              global.location.href = target;
            }, 450);
          })
          .catch(function (err) {
            setError((err && err.message) || "Upload failed.");
          });
      });
    }

    // Keyboard shortcuts when Record tab is visible
    document.addEventListener("keydown", function (ev) {
      var panel = $("sc-panel-record");
      if (!panel || panel.hidden) return;
      var tag = (ev.target && ev.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      var key = ev.key;
      if (key === "r" || key === "R") {
        if (recorder.state === "idle" && btnStart && !btnStart.hidden) {
          ev.preventDefault();
          btnStart.click();
        } else if (recorder.state === "stopped" && btnAgain) {
          ev.preventDefault();
          btnAgain.click();
        }
      } else if (key === "p" || key === "P") {
        if (recorder.state === "recording" && btnPause) {
          ev.preventDefault();
          btnPause.click();
        } else if (recorder.state === "paused" && btnResume) {
          ev.preventDefault();
          btnResume.click();
        }
      } else if (key === "s" || key === "S") {
        if (
          (recorder.state === "recording" || recorder.state === "paused") &&
          btnStop
        ) {
          ev.preventDefault();
          btnStop.click();
        }
      } else if (key === "Backspace" && (ev.metaKey || ev.ctrlKey)) {
        if (btnDelete && !btnDelete.disabled) {
          ev.preventDefault();
          btnDelete.click();
        }
      }
    });

    // Probe permission state without prompting when possible
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions
        .query({ name: "microphone" })
        .then(function (result) {
          if (result.state === "granted") setPermission("Microphone: Allowed ✓", "ok");
          else if (result.state === "denied")
            setPermission("Microphone: Denied ✗", "danger");
          else setPermission("Microphone: Not granted yet", "warn");
          result.onchange = function () {
            if (result.state === "granted") setPermission("Microphone: Allowed ✓", "ok");
            else if (result.state === "denied")
              setPermission("Microphone: Denied ✗", "danger");
          };
        })
        .catch(function () {
          setPermission("Microphone: Unknown", "neutral");
        });
    } else {
      setPermission("Microphone: Unknown", "neutral");
    }

    // Initial control visibility
    recorder._setState("idle");
  }

  function boot() {
    var cfgEl = $("sc-recorder-config");
    var config = {};
    if (cfgEl) {
      try {
        config = JSON.parse(cfgEl.textContent || "{}");
      } catch (_e) {
        config = {};
      }
    }
    initTabs();
    initFileDropzone();
    initRecorder(config);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  NS.initUploadPage = boot;
})(typeof window !== "undefined" ? window : this);
