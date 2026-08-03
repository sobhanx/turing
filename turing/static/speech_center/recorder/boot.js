/**
 * Wire Upload File + Record Audio tabs.
 * Both paths share the same MediaReviewEditor lifecycle (waveform, trim,
 * playback, metadata, upload progress).
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

  function formatBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function mimeToFormat(mime, name) {
    mime = String(mime || "").toLowerCase();
    name = String(name || "").toLowerCase();
    if (mime.indexOf("webm") >= 0 || name.endsWith(".webm")) return "WebM";
    if (mime.indexOf("ogg") >= 0 || name.endsWith(".ogg")) return "Ogg";
    if (mime.indexOf("wav") >= 0 || name.endsWith(".wav")) return "WAV";
    if (mime.indexOf("mpeg") >= 0 || mime.indexOf("mp3") >= 0 || name.endsWith(".mp3"))
      return "MP3";
    if (mime.indexOf("mp4") >= 0 || mime.indexOf("m4a") >= 0 || name.endsWith(".m4a"))
      return "M4A";
    if (mime.indexOf("flac") >= 0 || name.endsWith(".flac")) return "FLAC";
    return mime || "Audio";
  }

  function formatDurationMs(ms) {
    if (NS.VoiceRecorder && typeof NS.VoiceRecorder.formatDuration === "function") {
      return NS.VoiceRecorder.formatDuration(ms);
    }
    ms = Math.max(0, Math.floor(ms || 0));
    var totalSec = Math.floor(ms / 1000);
    var h = Math.floor(totalSec / 3600);
    var m = Math.floor((totalSec % 3600) / 60);
    var s = totalSec % 60;
    function pad(n) {
      return (n < 10 ? "0" : "") + n;
    }
    if (h > 0) return pad(h) + ":" + pad(m) + ":" + pad(s);
    return pad(m) + ":" + pad(s);
  }

  function editorLog(config, event, detail) {
    if (typeof console === "undefined") return;
    var payload = Object.assign({ scope: "MediaReviewEditor", event: event }, detail || {});
    var hasError = !!(detail && detail.error);
    if (hasError && console.warn) {
      console.warn("[TuringUploadEditor]", payload);
      return;
    }
    if (config && config.debug && console.info) {
      console.info("[TuringUploadEditor]", payload);
    }
  }

  function bindUploadProgressUI(opts) {
    var RecorderUploader = NS.RecorderUploader;
    var progressEl = opts.progressEl;
    var progressWrap = opts.progressWrap;
    var progressFill = opts.progressFill;
    var progressPct = opts.progressPct;
    var progressBar = opts.progressBar;
    var progressLabel = opts.progressLabel;
    var statusEl = opts.statusEl;
    var STATUS = opts.statusLabels || {};
    var lastBytes = null;

    function setStatus(state, detail) {
      if (!statusEl) return;
      var text = STATUS[state] || state;
      if (detail) text = text + (text ? " — " : "") + detail;
      statusEl.textContent = text;
      statusEl.dataset.state = state;
    }

    function formatPctLine(pct, loaded, total) {
      var line = pct + "%";
      if (typeof loaded === "number" && typeof total === "number" && total > 0) {
        line += " · " + formatBytes(loaded) + " / " + formatBytes(total);
      }
      return line;
    }

    function setBar(fill) {
      fill = Math.max(0, Math.min(100, fill || 0));
      if (progressFill) progressFill.style.width = fill + "%";
      if (progressBar) progressBar.setAttribute("aria-valuenow", String(fill));
    }

    function setStep(step, pct) {
      if (progressWrap) progressWrap.hidden = false;
      if (progressEl) progressEl.hidden = false;
      var order = RecorderUploader.STEPS;
      var idx = order.indexOf(step);
      if (progressEl) {
        progressEl.querySelectorAll("[data-step]").forEach(function (li) {
          var s = li.getAttribute("data-step");
          var si = order.indexOf(s);
          li.classList.toggle("is-done", si >= 0 && si < idx);
          li.classList.toggle("is-active", s === step);
          li.classList.toggle("is-failed", step === "failed" && s === "uploading");
          var meta = li.querySelector(".sc-rec-step-meta");
          if (meta) {
            if (s === "uploading" && typeof pct === "number") {
              meta.textContent =
                lastBytes && lastBytes.total
                  ? formatPctLine(pct, lastBytes.loaded, lastBytes.total)
                  : pct + "%";
            } else {
              meta.textContent = "";
            }
          }
        });
      }

      var fill = 0;
      if (step === "uploading" && typeof pct === "number") {
        fill = Math.min(99, Math.max(0, pct));
      } else if (step === "complete" || step === "redirecting") {
        fill = 100;
      }
      setBar(fill);

      if (progressPct) {
        if (step === "uploading" && typeof pct === "number") {
          progressPct.hidden = false;
          progressPct.textContent =
            lastBytes && lastBytes.total
              ? formatPctLine(pct, lastBytes.loaded, lastBytes.total)
              : pct + "%";
        } else if (step === "complete" || step === "redirecting") {
          progressPct.hidden = false;
          progressPct.textContent = "100%";
        } else if (step === "preparing") {
          progressPct.hidden = false;
          progressPct.textContent = "0%";
        } else if (step === "failed") {
          progressPct.hidden = true;
        }
      }
      if (progressLabel) {
        if (step === "uploading") progressLabel.textContent = "Uploading file...";
        else if (step === "complete") progressLabel.textContent = "✓ Upload completed";
        else if (step === "failed") progressLabel.textContent = "Upload failed";
        else if (step === "preparing") progressLabel.textContent = "Preparing audio…";
        else if (step === "redirecting")
          progressLabel.textContent = "Opening Create Transcript…";
      }
      if (step === "preparing") {
        lastBytes = null;
        setStatus("uploading", "Preparing…");
      } else if (step === "uploading") {
        setStatus(
          "uploading",
          typeof pct === "number"
            ? "Uploading file... " +
                (lastBytes && lastBytes.total
                  ? formatPctLine(pct, lastBytes.loaded, lastBytes.total)
                  : pct + "%")
            : "Uploading file..."
        );
      } else if (step === "complete") setStatus("completed", "✓ Upload completed");
      else if (step === "redirecting") setStatus("completed", "Opening Create Transcript…");
      else if (step === "failed") setStatus("failed");
    }

    function setUploadProgress(pct, loaded, total) {
      lastBytes = { loaded: loaded, total: total };
      setStep("uploading", pct);
    }

    function hideProgress() {
      lastBytes = null;
      if (progressWrap) progressWrap.hidden = true;
      if (progressFill) progressFill.style.width = "0%";
      if (progressPct) progressPct.hidden = true;
      if (progressBar) progressBar.setAttribute("aria-valuenow", "0");
    }

    return {
      setStep: setStep,
      setStatus: setStatus,
      setUploadProgress: setUploadProgress,
      hideProgress: hideProgress,
    };
  }

  function bindTrimPlayback(opts) {
    var audioEl = opts.audioEl;
    var wave = opts.wave;
    var btnPlay = opts.btnPlay;
    var positionEl = opts.positionEl;
    if (!audioEl) return;

    function formatPos(sec) {
      return formatDurationMs((sec || 0) * 1000);
    }

    function updatePlayBtn() {
      if (!btnPlay) return;
      btnPlay.textContent = audioEl.paused ? "▶ Play" : "⏸ Pause";
    }

    function updatePosition() {
      var t = audioEl.currentTime || 0;
      if (wave) wave.setPlayhead(t);
      if (positionEl) positionEl.textContent = "Position: " + formatPos(t);
    }

    audioEl.addEventListener("timeupdate", function () {
      updatePosition();
      if (!wave || audioEl.paused) return;
      var trim = wave.getTrim();
      if (trim.durationSec > 0 && audioEl.currentTime >= trim.endSec - 0.03) {
        audioEl.pause();
        try {
          audioEl.currentTime = trim.startSec;
        } catch (_e) {
          /* ignore */
        }
        wave.setPlayhead(trim.startSec);
        updatePlayBtn();
        updatePosition();
      }
    });
    audioEl.addEventListener("play", updatePlayBtn);
    audioEl.addEventListener("pause", updatePlayBtn);
    audioEl.addEventListener("ended", function () {
      updatePlayBtn();
      if (wave) {
        var trim = wave.getTrim();
        try {
          audioEl.currentTime = trim.startSec;
        } catch (_e2) {
          /* ignore */
        }
        wave.setPlayhead(trim.startSec);
      }
      updatePosition();
    });

    if (btnPlay) {
      btnPlay.addEventListener("click", function () {
        if (audioEl.hidden) audioEl.hidden = false;
        if (audioEl.paused) {
          if (wave) {
            var trim = wave.getTrim();
            var t = audioEl.currentTime || 0;
            if (t < trim.startSec || t >= trim.endSec - 0.05) {
              try {
                audioEl.currentTime = trim.startSec;
              } catch (_e3) {
                /* ignore */
              }
              wave.setPlayhead(trim.startSec);
            }
          }
          audioEl.play().catch(function (err) {
            editorLog(opts.config, "playback", {
              error: String((err && err.message) || err),
            });
          });
        } else {
          audioEl.pause();
        }
      });
    }
  }

  function mimeFromFilename(name) {
    name = String(name || "").toLowerCase();
    if (name.endsWith(".mp3")) return "audio/mpeg";
    if (name.endsWith(".wav")) return "audio/wav";
    if (name.endsWith(".m4a") || name.endsWith(".mp4")) return "audio/mp4";
    if (name.endsWith(".aac")) return "audio/aac";
    if (name.endsWith(".ogg") || name.endsWith(".oga")) return "audio/ogg";
    if (name.endsWith(".flac")) return "audio/flac";
    if (name.endsWith(".webm")) return "audio/webm";
    return "";
  }

  /**
   * HTMLAudioElement uses the Blob's type as Content-Type for blob: URLs.
   * An empty File.type often prevents loadedmetadata from ever firing.
   */
  function ensureTypedBlob(blob) {
    if (!blob) return blob;
    var type = (blob.type || "").trim() || mimeFromFilename(blob.name);
    if (!type) return blob;
    if ((blob.type || "").trim() === type) return blob;
    return new Blob([blob], { type: type });
  }

  function audioDebugSnapshot(audio) {
    if (!audio) return { error: "no audio element" };
    var err = audio.error;
    return {
      src: audio.src || "",
      currentSrc: audio.currentSrc || "",
      hidden: !!audio.hidden,
      preload: audio.preload || "",
      duration: audio.duration,
      networkState: audio.networkState,
      readyState: audio.readyState,
      error: err
        ? { code: err.code, message: err.message || String(err.code) }
        : null,
    };
  }

  /**
   * Shared review editor used by Upload File and Record Audio.
   * Owns waveform init, metadata, trim labels, playback, and export.
   *
   * Load order (required):
   *   1) createObjectURL + audio.src + audio.load()
   *   2) wait for loadedmetadata + canplay
   *   3) populate metadata
   *   4) init WaveformEditor / trim
   */
  function createReviewEditor(opts) {
    var WaveformEditor = NS.WaveformEditor;
    var canvas = opts.canvas;
    var audioEl = opts.audioEl;
    var reviewEl = opts.reviewEl;
    var trimLabel = opts.trimLabel;
    var durEl = opts.durEl;
    var formatEl = opts.formatEl;
    var sizeEl = opts.sizeEl;
    var orgLabel = opts.orgLabel;
    var orgSelect = opts.orgSelect;
    var positionEl = opts.positionEl;
    var btnPlay = opts.btnPlay;
    var btnTrim = opts.btnTrim;
    var btnSave = opts.btnSave;
    var titleEl = opts.titleEl;
    var cardState = opts.cardState;
    var config = opts.config || {};
    var fullLabel = opts.fullLabel || "Full media";
    var sourceBlob = null;
    var playbackObjectUrl = null;
    var ready = false;
    var wave = null;
    var playbackBound = false;
    var loadGeneration = 0;

    function log(event, detail) {
      editorLog(config, event, detail);
    }

    function updateTrimLabel(trim) {
      if (!trimLabel) return;
      if (!trim || !(trim.durationSec > 0)) {
        trimLabel.textContent = fullLabel;
        return;
      }
      trimLabel.textContent =
        "Selection " +
        formatDurationMs(trim.startSec * 1000) +
        " – " +
        formatDurationMs(trim.endSec * 1000) +
        " (" +
        formatDurationMs(trim.durationSec * 1000) +
        ")";
      if (durEl) durEl.textContent = formatDurationMs(trim.durationSec * 1000);
    }

    function updateOrgLabel() {
      if (!orgLabel || !orgSelect) return;
      var opt = orgSelect.options[orgSelect.selectedIndex];
      orgLabel.textContent = opt && opt.value ? String(opt.textContent || "").trim() : "—";
    }

    function setSaveEnabled(enabled) {
      if (!btnSave) return;
      var orgOk = !!(orgSelect && orgSelect.value && !orgSelect.disabled);
      btnSave.disabled = !(enabled && orgOk);
    }

    function ensureWave() {
      if (wave) return wave;
      if (!canvas || !WaveformEditor) {
        log("waveform_init", { error: "WaveformEditor or canvas missing" });
        return null;
      }
      try {
        wave = new WaveformEditor(canvas, {
          onChange: function (trim) {
            updateTrimLabel(trim);
          },
        });
        if (!playbackBound) {
          bindTrimPlayback({
            audioEl: audioEl,
            wave: wave,
            btnPlay: btnPlay,
            positionEl: positionEl,
            config: config,
          });
          playbackBound = true;
        }
        log("waveform_init", { ok: true });
      } catch (err) {
        wave = null;
        log("waveform_init", { error: String((err && err.message) || err) });
      }
      return wave;
    }

    function revokePlaybackUrl() {
      if (playbackObjectUrl) {
        if (typeof console !== "undefined" && console.log) {
          console.log("[sc-audio-diag] revokePlaybackUrl()", {
            url: playbackObjectUrl,
            audioSrcBefore: audioEl ? audioEl.src : null,
            stack: new Error("revoke stack").stack,
          });
        }
        try {
          URL.revokeObjectURL(playbackObjectUrl);
        } catch (_e) {
          /* ignore */
        }
        playbackObjectUrl = null;
      }
      if (audioEl) {
        audioEl.removeAttribute("src");
        try {
          audioEl.removeAttribute("srcObject");
        } catch (_e2) {
          /* ignore */
        }
      }
    }

    function waitForAudioReady(audio, timeoutMs) {
      return new Promise(function (resolve, reject) {
        var settled = false;
        var sawMetadata = false;
        var timer = null;

        function cleanup() {
          audio.removeEventListener("loadedmetadata", onMeta);
          audio.removeEventListener("durationchange", onMeta);
          audio.removeEventListener("canplay", onCanPlay);
          audio.removeEventListener("error", onError);
          if (timer) {
            clearTimeout(timer);
            timer = null;
          }
        }

        function durationOk() {
          return isFinite(audio.duration) && audio.duration > 0;
        }

        function tryResolve() {
          if (settled) return;
          if (!sawMetadata || !durationOk()) return;
          // canplay => readyState >= HAVE_FUTURE_DATA (3) in practice; accept >= 2.
          if (audio.readyState < 2) return;
          settled = true;
          cleanup();
          log("audio_ready", audioDebugSnapshot(audio));
          resolve(audio.duration);
        }

        function onMeta() {
          sawMetadata = true;
          log("loadedmetadata", audioDebugSnapshot(audio));
          tryResolve();
        }

        function onCanPlay() {
          log("canplay", audioDebugSnapshot(audio));
          tryResolve();
        }

        function fail(reason) {
          if (settled) return;
          settled = true;
          cleanup();
          var snap = audioDebugSnapshot(audio);
          log("audio_load_failed", Object.assign({ reason: reason }, snap));
          // Explicit diagnostics required when loadedmetadata never fires.
          log("audio_src", { src: snap.src, currentSrc: snap.currentSrc });
          log("audio_error", { error: snap.error });
          log("audio_networkState", { networkState: snap.networkState });
          log("audio_readyState", { readyState: snap.readyState });
          reject(new Error(reason));
        }

        function onError() {
          fail("HTMLAudioElement error while loading selected file.");
        }

        audio.addEventListener("loadedmetadata", onMeta);
        audio.addEventListener("durationchange", onMeta);
        audio.addEventListener("canplay", onCanPlay);
        audio.addEventListener("error", onError);

        if (durationOk() && audio.readyState >= 2) {
          sawMetadata = true;
          tryResolve();
        }

        timer = global.setTimeout(function () {
          fail("loadedmetadata/canplay did not fire for selected file.");
        }, timeoutMs || 20000);
      });
    }

    /**
     * Assign blob URL to the audio element and wait until it can play.
     * Must not revoke the active object URL until a replacement is assigned.
     *
     * Always-on console diagnostics (not gated by config.debug) — required to
     * determine why loadedmetadata may never fire in a given browser session.
     */
    function syncPlayback(blob) {
      if (!audioEl) {
        return Promise.reject(new Error("Playback <audio> element missing."));
      }
      if (!blob) {
        return Promise.reject(new Error("No audio blob to load."));
      }

      var typed = ensureTypedBlob(blob);
      var previousUrl = playbackObjectUrl;
      var objectUrl = URL.createObjectURL(typed);
      playbackObjectUrl = objectUrl;

      if (typeof console !== "undefined" && console.log) {
        console.log("[sc-audio-diag] objectURL created", {
          objectUrl: objectUrl,
          isBlobUrl: String(objectUrl).indexOf("blob:") === 0,
          fileName: blob.name || "",
          fileType: blob.type || "",
          playbackType: typed.type || "",
          size: typed.size,
          audioElementId: audioEl.id,
          audioIsConnected: !!audioEl.isConnected,
          sameAsGetElementById:
            audioEl === document.getElementById("sc-file-playback") ||
            audioEl === document.getElementById("sc-rec-playback"),
          audioElementsInDom: document.querySelectorAll("audio").length,
        });
      }

      // display:none / hidden media often never reaches loadedmetadata.
      if (reviewEl) {
        reviewEl.hidden = false;
        reviewEl.removeAttribute("hidden");
      }
      audioEl.hidden = false;
      audioEl.removeAttribute("hidden");
      audioEl.preload = "auto";

      // Watch for anything clearing/replacing src or the node itself.
      var mo = null;
      if (typeof MutationObserver !== "undefined") {
        mo = new MutationObserver(function (mutations) {
          mutations.forEach(function (m) {
            if (typeof console !== "undefined" && console.log) {
              console.log("[sc-audio-diag] DOM mutation on audio", {
                type: m.type,
                attributeName: m.attributeName,
                src: audioEl.getAttribute("src"),
                srcProp: audioEl.src,
                stillInDocument: document.contains(audioEl),
                liveById: document.getElementById(audioEl.id),
                liveIsSameNode: document.getElementById(audioEl.id) === audioEl,
              });
            }
          });
        });
        mo.observe(audioEl, { attributes: true, attributeFilter: ["src", "hidden", "preload"] });
        if (audioEl.parentNode) {
          mo.observe(audioEl.parentNode, { childList: true, subtree: false });
        }
      }

      var diagEvents = [
        "loadedmetadata",
        "loadeddata",
        "canplay",
        "canplaythrough",
        "durationchange",
        "error",
        "abort",
        "stalled",
        "emptied",
      ];
      var seenEvents = [];
      var sawLoadedMetadata = false;
      function onDiagEvent(ev) {
        seenEvents.push(ev.type);
        if (ev.type === "loadedmetadata") sawLoadedMetadata = true;
        if (typeof console !== "undefined" && console.log) {
          console.log("[sc-audio-diag] event:" + ev.type, {
            src: audioEl.src,
            currentSrc: audioEl.currentSrc,
            readyState: audioEl.readyState,
            networkState: audioEl.networkState,
            error: audioEl.error,
            paused: audioEl.paused,
            duration: audioEl.duration,
            objectUrlStillMatches:
              audioEl.src === objectUrl || audioEl.currentSrc === objectUrl,
          });
        }
      }
      diagEvents.forEach(function (name) {
        audioEl.addEventListener(name, onDiagEvent);
      });

      audioEl.src = objectUrl;

      // Exact snapshot requested — immediately after assigning the audio source.
      if (typeof console !== "undefined" && console.log) {
        console.log({
          src: audioEl.src,
          currentSrc: audioEl.currentSrc,
          readyState: audioEl.readyState,
          networkState: audioEl.networkState,
          error: audioEl.error,
          paused: audioEl.paused,
        });
        console.log("[sc-audio-diag] after src assign", {
          objectUrl: objectUrl,
          srcEqualsObjectUrl: audioEl.src === objectUrl,
          currentSrcEqualsObjectUrl: audioEl.currentSrc === objectUrl,
          parentHidden: reviewEl ? !!reviewEl.hidden : null,
          parentDisplay: reviewEl
            ? global.getComputedStyle(reviewEl).display
            : null,
          audioDisplay: global.getComputedStyle(audioEl).display,
        });
      }

      // Revoke the previous URL only after the new src is set.
      if (previousUrl && previousUrl !== objectUrl) {
        if (typeof console !== "undefined" && console.log) {
          console.log("[sc-audio-diag] revoking PREVIOUS object URL only", {
            previousUrl: previousUrl,
            activeUrl: objectUrl,
          });
        }
        try {
          URL.revokeObjectURL(previousUrl);
        } catch (_e) {
          /* ignore */
        }
      }

      var readyPromise = waitForAudioReady(audioEl, 20000);
      try {
        audioEl.load();
        if (typeof console !== "undefined" && console.log) {
          console.log("[sc-audio-diag] audio.load() called", {
            src: audioEl.src,
            currentSrc: audioEl.currentSrc,
            readyState: audioEl.readyState,
            networkState: audioEl.networkState,
            error: audioEl.error,
            paused: audioEl.paused,
          });
        }
      } catch (err) {
        if (typeof console !== "undefined" && console.log) {
          console.log("[sc-audio-diag] audio.load() threw", String(err && err.message || err));
        }
      }

      // Watchdog: if loadedmetadata never fires, print the exact failure class.
      global.setTimeout(function () {
        if (sawLoadedMetadata) return;
        var live = document.getElementById(audioEl.id);
        var diagnosis = "unknown";
        if (!audioEl.src) {
          diagnosis = "src_cleared_after_assign";
        } else if (audioEl.src !== objectUrl) {
          diagnosis = "src_replaced_with_different_value";
        } else if (live !== audioEl) {
          diagnosis = "audio_node_replaced_or_removed";
        } else if (!document.contains(audioEl)) {
          diagnosis = "audio_node_detached_from_document";
        } else if (audioEl.error && audioEl.error.code === 4) {
          diagnosis =
            "MEDIA_ERR_SRC_NOT_SUPPORTED — often blob URL revoked too early or unsupported type";
        } else if (audioEl.error) {
          diagnosis = "media_element_error_code_" + audioEl.error.code;
        } else if (!audioEl.currentSrc) {
          diagnosis =
            "currentSrc_empty_while_src_set — resource selection never committed (aborted/stalled/revoked)";
        } else if (seenEvents.indexOf("abort") >= 0 || seenEvents.indexOf("emptied") >= 0) {
          diagnosis = "load_aborted_or_emptied_without_metadata";
        } else if (document.querySelectorAll("audio").length > 2) {
          diagnosis = "unexpected_extra_audio_elements_in_dom";
        } else {
          diagnosis =
            "loadedmetadata_never_fired_with_src_intact — inspect networkState/readyState/events above";
        }
        if (typeof console !== "undefined" && console.log) {
          console.log("[sc-audio-diag] loadedmetadata NEVER FIRED — diagnosis", {
            diagnosis: diagnosis,
            objectUrl: objectUrl,
            src: audioEl.src,
            currentSrc: audioEl.currentSrc,
            readyState: audioEl.readyState,
            networkState: audioEl.networkState,
            error: audioEl.error,
            paused: audioEl.paused,
            duration: audioEl.duration,
            seenEvents: seenEvents.slice(),
            playbackObjectUrlActive: playbackObjectUrl,
            activeUrlStillThisObjectUrl: playbackObjectUrl === objectUrl,
            audioElements: Array.prototype.map.call(
              document.querySelectorAll("audio"),
              function (a) {
                return { id: a.id, src: a.src, currentSrc: a.currentSrc, duration: a.duration };
              }
            ),
            liveNodeIsSame: live === audioEl,
          });
        }
      }, 3000);

      return readyPromise.then(
        function (durationSec) {
          diagEvents.forEach(function (name) {
            audioEl.removeEventListener(name, onDiagEvent);
          });
          if (mo) mo.disconnect();
          return durationSec;
        },
        function (err) {
          diagEvents.forEach(function (name) {
            audioEl.removeEventListener(name, onDiagEvent);
          });
          if (mo) mo.disconnect();
          throw err;
        }
      );
    }

    function applySyncMetadata(blob, meta) {
      meta = meta || {};
      var name = meta.name || (blob && blob.name) || "";
      var mime = meta.mime || (blob && blob.type) || mimeFromFilename(name) || "";
      if (titleEl && name) titleEl.textContent = name;
      if (cardState) {
        cardState.textContent = meta.cardState || "Review before uploading";
      }
      if (formatEl) formatEl.textContent = mimeToFormat(mime, name);
      if (sizeEl) sizeEl.textContent = formatBytes(blob && blob.size);
      if (typeof meta.durationMs === "number" && meta.durationMs >= 0 && durEl) {
        durEl.textContent = formatDurationMs(meta.durationMs);
      }
      updateOrgLabel();
      if (positionEl) positionEl.textContent = "Position: " + formatDurationMs(0);
      if (trimLabel) trimLabel.textContent = fullLabel;
    }

    function showReview() {
      if (!reviewEl) return;
      reviewEl.hidden = false;
      reviewEl.removeAttribute("hidden");
      if (canvas) void canvas.offsetWidth;
    }

    function setLoadingPlaceholders() {
      if (durEl) durEl.textContent = "…";
      if (formatEl) formatEl.textContent = "…";
      if (sizeEl) sizeEl.textContent = "…";
      if (positionEl) positionEl.textContent = "Position: …";
      if (trimLabel) trimLabel.textContent = "Loading audio…";
      updateOrgLabel();
    }

    function loadSource(blob, meta) {
      meta = meta || {};
      var thisLoad = ++loadGeneration;
      log("file_selection", {
        name: meta.name || (blob && blob.name) || "",
        size: blob && blob.size,
        type: (blob && blob.type) || "",
        loadId: thisLoad,
      });
      if (!blob) {
        ready = false;
        setSaveEnabled(false);
        log("file_selection", { error: "No File/Blob provided" });
        return Promise.reject(new Error("No file to load."));
      }

      sourceBlob = blob;
      ready = false;
      setSaveEnabled(false);
      showReview();
      setLoadingPlaceholders();
      if (titleEl) {
        titleEl.textContent = meta.name || blob.name || "Selected file";
      }
      if (cardState) {
        cardState.textContent = meta.cardState || "Loading audio…";
      }

      // 1–2) Object URL + load, wait for loadedmetadata/canplay BEFORE waveform.
      return syncPlayback(blob)
        .then(function (durationSec) {
          if (thisLoad !== loadGeneration) {
            log("load_superseded", { loadId: thisLoad });
            return null;
          }
          if (!(isFinite(durationSec) && durationSec > 0)) {
            throw new Error("audio.duration is not a valid number after load.");
          }

          // 3) Metadata only after loadedmetadata succeeded.
          applySyncMetadata(blob, Object.assign({}, meta, {
            durationMs: durationSec * 1000,
            mime: (blob.type || "").trim() || mimeFromFilename(blob.name),
          }));
          if (cardState) {
            cardState.textContent = meta.cardState || "Review before uploading";
          }

          // 4) Waveform + trim only after audio element is ready.
          ensureWave();
          if (!wave) {
            log("waveform_decode", { error: "Waveform editor not initialized" });
            return blob;
          }
          return wave
            .loadBlob(blob)
            .then(function (audioBuffer) {
              if (thisLoad !== loadGeneration) return null;
              var durSec =
                (wave && wave.durationSec) ||
                (audioBuffer && audioBuffer.duration) ||
                durationSec ||
                0;
              if (durEl && durSec > 0) {
                durEl.textContent = formatDurationMs(durSec * 1000);
              }
              if (wave.setPlayhead) wave.setPlayhead(0);
              updateTrimLabel(wave.getTrim());
              log("waveform_decode", {
                ok: true,
                durationSec: durSec,
                peaks: wave.peaks ? wave.peaks.length : 0,
              });
              return audioBuffer;
            })
            .catch(function (err) {
              log("waveform_decode", {
                error: String((err && err.message) || err),
                cause: "AudioContext/decodeAudioData",
              });
              if (trimLabel) {
                trimLabel.textContent =
                  "Waveform preview unavailable — you can still upload the full file";
              }
              // Audio element already loaded; upload can proceed.
              return null;
            });
        })
        .then(function () {
          if (thisLoad !== loadGeneration) return sourceBlob;
          ready = true;
          setSaveEnabled(true);
          log("editor_ready", {
            ready: true,
            audioDuration: audioEl ? audioEl.duration : null,
            format: formatEl ? formatEl.textContent : "",
            size: sizeEl ? sizeEl.textContent : "",
            org: orgLabel ? orgLabel.textContent : "",
            src: audioEl ? audioEl.src : "",
          });
          if (typeof opts.onReady === "function") opts.onReady(sourceBlob);
          return sourceBlob;
        })
        .catch(function (err) {
          if (thisLoad !== loadGeneration) return sourceBlob;
          ready = false;
          setSaveEnabled(false);
          if (durEl) durEl.textContent = "—";
          if (trimLabel) trimLabel.textContent = "Audio failed to load";
          if (cardState) cardState.textContent = "Could not load audio";
          log("loadSource_failed", {
            error: String((err && err.message) || err),
            audio: audioDebugSnapshot(audioEl),
          });
          throw err;
        });
    }

    function clear() {
      loadGeneration += 1;
      sourceBlob = null;
      ready = false;
      revokePlaybackUrl();
      if (audioEl) {
        audioEl.hidden = true;
        try {
          audioEl.load();
        } catch (_e) {
          /* ignore */
        }
      }
      if (wave) wave.reset();
      if (reviewEl) {
        reviewEl.hidden = true;
        reviewEl.setAttribute("hidden", "");
      }
      if (durEl) durEl.textContent = "—";
      if (formatEl) formatEl.textContent = "—";
      if (sizeEl) sizeEl.textContent = "—";
      if (positionEl) positionEl.textContent = "Position: —";
      if (trimLabel) trimLabel.textContent = fullLabel;
      if (btnPlay) btnPlay.textContent = "▶ Play";
      updateOrgLabel();
      setSaveEnabled(false);
    }

    function exportForUpload() {
      log("exportBlob_start", {
        hasWave: !!wave,
        hasSource: !!sourceBlob,
        size: sourceBlob && sourceBlob.size,
        audioSrc: audioEl ? audioEl.src : "",
      });
      if (!sourceBlob) {
        var missing = new Error("No file selected.");
        log("exportBlob", { error: missing.message });
        return Promise.reject(missing);
      }
      if (!wave) return Promise.resolve(sourceBlob);
      return wave.exportBlob(sourceBlob).then(
        function (out) {
          log("exportBlob", {
            ok: true,
            outSize: out && out.size,
            type: out && out.type,
          });
          return out;
        },
        function (err) {
          log("exportBlob", { error: String((err && err.message) || err) });
          throw err;
        }
      );
    }

    if (btnTrim && canvas) {
      btnTrim.addEventListener("click", function () {
        showReview();
        canvas.scrollIntoView({ behavior: "smooth", block: "nearest" });
        try {
          canvas.focus();
        } catch (_e) {
          /* ignore */
        }
      });
    }

    if (orgSelect) {
      orgSelect.addEventListener("change", function () {
        updateOrgLabel();
        setSaveEnabled(ready);
      });
    }

    updateOrgLabel();
    setSaveEnabled(false);

    return {
      loadSource: loadSource,
      clear: clear,
      exportForUpload: exportForUpload,
      updateOrgLabel: updateOrgLabel,
      setSaveEnabled: setSaveEnabled,
      showReview: showReview,
      getSource: function () {
        return sourceBlob;
      },
      isReady: function () {
        return ready && !!sourceBlob;
      },
      getWave: function () {
        return wave;
      },
      getAudioElement: function () {
        return audioEl;
      },
    };
  }

  function initFileUpload(config) {
    var RecorderUploader = NS.RecorderUploader;
    var zone = $("sc-dropzone");
    var input = $("sc-file-input");
    var nameEl = $("sc-file-name");
    var hintEl = $("sc-file-hint");
    var form = $("sc-upload-form");
    var errorEl = $("sc-file-error");
    var successEl = $("sc-file-success");
    var btnSave = $("sc-file-save");
    var btnClear = $("sc-file-clear");
    var orgSelect = $("sc-file-org");

    if (!zone || !input || !form) {
      editorLog(config, "file_init", {
        error: "Missing dropzone, file input, or form",
      });
      return;
    }

    var progressUI = bindUploadProgressUI({
      progressEl: $("sc-file-progress"),
      progressWrap: $("sc-file-progress-wrap"),
      progressFill: $("sc-file-progress-fill"),
      progressPct: $("sc-file-progress-pct"),
      progressBar: $("sc-file-progress-bar"),
      progressLabel: $("sc-file-progress-label"),
      statusEl: $("sc-file-status"),
      statusLabels: {
        idle: "Select a file to continue",
        selected: "File selected — review and upload",
        uploading: "Uploading",
        completed: "Upload completed",
        failed: "Upload failed",
      },
    });

    function setError(msg) {
      if (!errorEl) return;
      if (!msg) {
        errorEl.hidden = true;
        errorEl.textContent = "";
        return;
      }
      errorEl.hidden = false;
      errorEl.textContent = msg;
      progressUI.setStatus("failed");
      editorLog(config, "upload_validation", { error: msg });
    }

    var editor = createReviewEditor({
      config: config,
      canvas: $("sc-file-waveform"),
      audioEl: $("sc-file-playback"),
      reviewEl: $("sc-file-review"),
      trimLabel: $("sc-file-trim-label"),
      durEl: $("sc-file-duration"),
      formatEl: $("sc-file-format"),
      sizeEl: $("sc-file-size"),
      orgLabel: $("sc-file-org-label"),
      orgSelect: orgSelect,
      positionEl: $("sc-file-position"),
      btnPlay: $("sc-file-play-focus"),
      btnTrim: $("sc-file-trim-focus"),
      btnSave: btnSave,
      titleEl: $("sc-file-card-title"),
      cardState: $("sc-file-card-state"),
      fullLabel: "Full file",
      onReady: function () {
        progressUI.setStatus("selected");
      },
    });

    var uploader = RecorderUploader
      ? new RecorderUploader({
          uploadUrl: config.uploadUrl,
          redirectUrl: config.redirectUrl,
          csrfToken: config.csrfToken,
          maxUploadBytes: config.maxUploadBytes,
          uploadSource: "file",
          onStep: progressUI.setStep,
          onProgress: progressUI.setUploadProgress,
          onError: setError,
        })
      : null;

    function clearSelection() {
      editor.clear();
      input.value = "";
      if (nameEl) {
        nameEl.hidden = true;
        nameEl.textContent = "";
      }
      if (hintEl) hintEl.hidden = false;
      if (successEl) successEl.hidden = true;
      progressUI.hideProgress();
      progressUI.setStatus("idle");
      setError("");
    }

    function showSelected(file) {
      if (!file) {
        clearSelection();
        return;
      }
      if (nameEl) {
        nameEl.hidden = false;
        nameEl.textContent =
          file.name + (file.size ? " · " + formatBytes(file.size) : "");
      }
      if (hintEl) hintEl.hidden = true;
      if (successEl) successEl.hidden = true;
      progressUI.hideProgress();
      setError("");
      // Optimistic status while metadata/waveform load.
      progressUI.setStatus("selected");
      editor
        .loadSource(file, {
          name: file.name,
          mime: file.type,
          cardState: "Review before uploading",
        })
        .catch(function (err) {
          setError((err && err.message) || "Could not load the selected file.");
        });
    }

    // Bind file input once — listening to both change+input double-fires loadSource
    // and the second pass revoked the blob: URL before loadedmetadata.
    function onFileInputChange() {
      var file = input.files && input.files[0];
      if (typeof console !== "undefined" && console.log) {
        console.log("[sc-audio-diag] file input change", {
          hasFile: !!file,
          name: file && file.name,
          size: file && file.size,
          type: file && file.type,
        });
      }
      editorLog(config, "file_input_change", {
        hasFile: !!file,
        name: file && file.name,
        size: file && file.size,
        type: file && file.type,
      });
      if (file) showSelected(file);
      else clearSelection();
    }
    input.addEventListener("change", onFileInputChange);

    zone.addEventListener("click", function (e) {
      // Input already covers the zone; only synthesize a click if needed.
      if (e.target === input) return;
      e.preventDefault();
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
      try {
        var dt = new DataTransfer();
        dt.items.add(files[0]);
        input.files = dt.files;
      } catch (err) {
        editorLog(config, "file_drop", {
          error: String((err && err.message) || err),
        });
      }
      showSelected(files[0]);
    });

    if (btnClear) btnClear.addEventListener("click", clearSelection);

    form.addEventListener("submit", function (e) {
      if (uploader && editor.getSource()) {
        e.preventDefault();
        if (btnSave) btnSave.click();
      }
    });

    if (btnSave) {
      btnSave.addEventListener("click", function () {
        setError("");
        var selectedFile = editor.getSource();
        if (!selectedFile || !editor.isReady()) {
          setError("Please choose an audio file to upload.");
          progressUI.setStatus("idle");
          return;
        }
        if (!orgSelect || !orgSelect.value) {
          setError("Please select an organization.");
          if (orgSelect) orgSelect.focus();
          return;
        }
        if (!uploader) {
          form.submit();
          return;
        }
        btnSave.disabled = true;
        if (successEl) successEl.hidden = true;
        progressUI.setStep("preparing", 0);
        editor
          .exportForUpload()
          .then(function (blob) {
            var mime =
              (blob && blob.type) || selectedFile.type || "application/octet-stream";
            var name = selectedFile.name || "upload.bin";
            if (
              blob !== selectedFile &&
              mime.indexOf("wav") >= 0 &&
              !/\.wav$/i.test(name)
            ) {
              name = name.replace(/\.[^.]+$/, "") + ".wav";
            }
            var file =
              blob instanceof File && blob.name
                ? blob
                : new File([blob], name, { type: mime, lastModified: Date.now() });
            return uploader.uploadFile(file, orgSelect.value);
          })
          .then(function (result) {
            if (successEl) {
              successEl.hidden = false;
              successEl.textContent =
                "✓ Upload completed: " + (result.filename || "");
            }
            progressUI.setStep("complete", 100);
            progressUI.setStep("redirecting", 100);
            var target = result.redirectUrl || config.redirectUrl;
            global.setTimeout(function () {
              global.location.href = target;
            }, 450);
          })
          .catch(function (err) {
            editor.setSaveEnabled(true);
            setError((err && err.message) || "Upload failed.");
            progressUI.setStep("failed", 0);
          });
      });
    }

    // If the browser restored a previously chosen file, hydrate the editor.
    if (input.files && input.files[0]) {
      showSelected(input.files[0]);
    } else {
      progressUI.setStatus("idle");
      editor.setSaveEnabled(false);
    }

    editorLog(config, "file_init", { ok: true });
  }

  function initRecorder(config) {
    var VoiceRecorder = NS.VoiceRecorder;
    var RecorderUploader = NS.RecorderUploader;
    if (!VoiceRecorder || !RecorderUploader) {
      editorLog(config, "recorder_init", {
        error: "VoiceRecorder or RecorderUploader missing",
      });
      return;
    }

    var root = $("sc-recorder");
    if (!root) return;

    var permEl = $("sc-rec-permission");
    var timerEl = $("sc-rec-timer");
    var errorEl = $("sc-rec-error");
    var unsupportedEl = $("sc-rec-unsupported");
    var controls = $("sc-rec-controls");
    var review = $("sc-rec-review");
    var fileOrgSelect = $("sc-file-org");
    var orgSelect =
      $("sc-rec-org") ||
      document.querySelector('#sc-panel-record [name="organization_id"], #sc-rec-org');

    var btnStart = $("sc-rec-start");
    var btnPause = $("sc-rec-pause");
    var btnResume = $("sc-rec-resume");
    var btnStop = $("sc-rec-stop");
    var btnDelete = $("sc-rec-delete");
    var btnAgain = $("sc-rec-again");
    var btnSave = $("sc-rec-save");

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

    var progressUI = bindUploadProgressUI({
      progressEl: $("sc-rec-progress"),
      progressWrap: $("sc-rec-progress-wrap"),
      progressFill: $("sc-rec-progress-fill"),
      progressPct: $("sc-rec-progress-pct"),
      progressBar: $("sc-rec-progress-bar"),
      progressLabel: $("sc-rec-progress-label"),
      statusEl: $("sc-rec-status"),
      statusLabels: STATUS_LABELS,
    });

    function setError(msg) {
      if (!errorEl) return;
      if (!msg) {
        errorEl.hidden = true;
        errorEl.textContent = "";
        return;
      }
      errorEl.hidden = false;
      errorEl.textContent = msg;
      progressUI.setStatus("failed");
      editorLog(config, "upload_validation", { error: msg });
    }

    function setPermission(text, tone) {
      if (!permEl) return;
      permEl.textContent = text;
      permEl.dataset.tone = tone || "neutral";
    }

    function syncOrgSelects(source) {
      var value = source && source.value ? source.value : "";
      if (orgSelect && source !== orgSelect) orgSelect.value = value;
      if (fileOrgSelect && source !== fileOrgSelect) fileOrgSelect.value = value;
      editor.updateOrgLabel();
    }

    function requireOrg() {
      if (!orgSelect || !orgSelect.value) {
        setError("Select an organization before recording or uploading.");
        if (orgSelect) orgSelect.focus();
        return false;
      }
      return true;
    }

    function showSuccess(filename) {
      var el = $("sc-rec-success");
      if (!el) return;
      el.hidden = false;
      el.textContent = filename
        ? "Recording uploaded successfully: " + filename
        : "Recording uploaded successfully";
      progressUI.setStatus("completed");
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

    var editor = createReviewEditor({
      config: config,
      canvas: $("sc-rec-waveform"),
      audioEl: $("sc-rec-playback"),
      reviewEl: review,
      trimLabel: $("sc-rec-trim-label"),
      durEl: $("sc-rec-duration"),
      formatEl: $("sc-rec-format"),
      sizeEl: $("sc-rec-size"),
      orgLabel: $("sc-rec-org-label"),
      orgSelect: orgSelect,
      positionEl: $("sc-rec-position"),
      btnPlay: $("sc-rec-play-focus"),
      btnTrim: $("sc-rec-trim-focus"),
      btnSave: btnSave,
      titleEl: null,
      cardState: $("sc-rec-card-state"),
      fullLabel: "Full recording",
    });

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
        progressUI.setStatus(state);
        if (state === "denied") {
          setPermission("Microphone: Denied ✗", "danger");
          setError(
            "Microphone access was denied. Allow the microphone in your browser settings and try again."
          );
        } else if (state === "requesting") {
          setPermission("Microphone: Requesting…", "warn");
        } else if (
          state === "recording" ||
          state === "paused" ||
          state === "stopped"
        ) {
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
      onTick: function (ms) {
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

    var uploader = new RecorderUploader({
      uploadUrl: config.uploadUrl,
      redirectUrl: config.redirectUrl,
      csrfToken: config.csrfToken,
      maxUploadBytes: config.maxUploadBytes,
      uploadSource: "recorder",
      onStep: progressUI.setStep,
      onProgress: progressUI.setUploadProgress,
      onError: setError,
    });

    function afterStop(blob) {
      var active = blob || recorder.blob;
      editor
        .loadSource(active, {
          name: "Recording",
          mime: active && active.type,
          durationMs: recorder.elapsedMs,
          cardState: "Review your recording",
        })
        .catch(function (err) {
          setError((err && err.message) || "Could not load recording for review.");
        });
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

    if (btnStart) {
      btnStart.addEventListener("click", function () {
        if (!requireOrg()) return;
        setError("");
        progressUI.hideProgress();
        recorder.start().catch(function (err) {
          setError((err && err.message) || "Could not start recording.");
        });
      });
    }
    if (btnPause)
      btnPause.addEventListener("click", function () {
        recorder.pause();
      });
    if (btnResume)
      btnResume.addEventListener("click", function () {
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
        });
      });
    }
    if (btnDelete) {
      btnDelete.addEventListener("click", function () {
        editor.clear();
        if (review) review.hidden = true;
        recorder.deleteRecording();
        if (timerEl) timerEl.textContent = "00:00:00";
        progressUI.hideProgress();
        setError("");
      });
    }
    if (btnAgain) {
      btnAgain.addEventListener("click", function () {
        if (!requireOrg()) return;
        editor.clear();
        if (review) review.hidden = true;
        progressUI.hideProgress();
        setError("");
        recorder.recordAgain().catch(function (err) {
          setError((err && err.message) || "Could not restart recording.");
        });
      });
    }
    if (btnSave) {
      btnSave.addEventListener("click", function () {
        setError("");
        if (!requireOrg()) return;
        var source = editor.getSource() || recorder.blob;
        if (!source) {
          setError("Nothing to upload yet.");
          return;
        }
        if (!assertUploadableBlob(source, recorder.elapsedMs)) {
          if (btnSave) btnSave.disabled = true;
          return;
        }
        var orgId = orgSelect ? orgSelect.value : "";
        progressUI.setStep("preparing", 0);
        editor
          .exportForUpload()
          .then(function (blob) {
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
            progressUI.setStep("complete", 100);
            progressUI.setStep("redirecting", 100);
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

    recorder._setState("idle");
  }

  function boot() {
    if (typeof console !== "undefined" && console.log) {
      console.log("UPLOAD REVIEW V2 boot", {
        templateMarker: !!(document.getElementById("sc-upload-review-v2-marker")),
        markerText: (document.getElementById("sc-upload-review-v2-marker") || {}).textContent,
        bootScript: (document.querySelector('script[src*="recorder/boot.js"]') || {}).src || null,
        hasCreateReviewEditor: typeof NS.createReviewEditor === "function",
        fileReviewEl: !!document.getElementById("sc-file-review"),
        filePlaybackEl: !!document.getElementById("sc-file-playback"),
      });
    }
    var root = document.getElementById("sc-upload-root");
    if (root) root.setAttribute("data-sc-boot", "upload-review-v2");

    var cfgEl = $("sc-recorder-config");
    var config = {};
    if (cfgEl) {
      try {
        config = JSON.parse(cfgEl.textContent || "{}");
      } catch (err) {
        editorLog({}, "boot_config", {
          error: String((err && err.message) || err),
        });
        config = {};
      }
    }
    try {
      initTabs();
      initFileUpload(config);
      initRecorder(config);
      if (typeof console !== "undefined" && console.log) {
        console.log("UPLOAD REVIEW V2 createReviewEditor ready", {
          exported: typeof NS.createReviewEditor === "function",
          fileStatus: (document.getElementById("sc-file-status") || {}).textContent,
        });
      }
    } catch (err) {
      if (typeof console !== "undefined" && console.error) {
        console.error("UPLOAD REVIEW V2 boot failed", err);
      }
      editorLog(config, "boot", {
        error: String((err && err.message) || err),
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  NS.createReviewEditor = createReviewEditor;
  NS.initUploadPage = boot;
})(typeof window !== "undefined" ? window : this);
