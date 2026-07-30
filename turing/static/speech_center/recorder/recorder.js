/**
 * Browser MediaRecorder wrapper for Speech Center.
 * Produces a Blob suitable for the existing upload form / MediaService path.
 */
(function (global) {
  "use strict";

  var NS = (global.TuringSpeechCenter = global.TuringSpeechCenter || {});

  function pickMimeType(preferred) {
    if (!global.MediaRecorder || typeof MediaRecorder.isTypeSupported !== "function") {
      return "";
    }
    var list = preferred || [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/ogg",
    ];
    for (var i = 0; i < list.length; i++) {
      if (MediaRecorder.isTypeSupported(list[i])) return list[i];
    }
    return "";
  }

  function extensionForMime(mime) {
    var base = (mime || "").split(";")[0].trim().toLowerCase();
    if (base.indexOf("ogg") !== -1) return "ogg";
    return "webm";
  }

  function formatDuration(ms) {
    ms = Math.max(0, Math.floor(ms || 0));
    var totalSec = Math.floor(ms / 1000);
    var m = Math.floor(totalSec / 60);
    var s = totalSec % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  function VoiceRecorder(options) {
    this.options = options || {};
    this.preferredMimeTypes = this.options.preferredMimeTypes || null;
    this.mimeType = pickMimeType(this.preferredMimeTypes);
    this.stream = null;
    this.mediaRecorder = null;
    this.chunks = [];
    this.blob = null;
    this.state = "idle"; // idle | requesting | recording | paused | stopped | denied | unsupported
    this.startedAt = 0;
    this.elapsedMs = 0;
    this._tickTimer = null;
    this._pausedAt = 0;
    this._accumPaused = 0;
    this.onStateChange = this.options.onStateChange || function () {};
    this.onTick = this.options.onTick || function () {};
    this.onError = this.options.onError || function () {};
  }

  VoiceRecorder.isSupported = function () {
    return !!(
      global.navigator &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function" &&
      global.MediaRecorder
    );
  };

  VoiceRecorder.prototype.getMimeType = function () {
    return this.mimeType || pickMimeType(this.preferredMimeTypes);
  };

  VoiceRecorder.prototype._setState = function (state) {
    this.state = state;
    this.onStateChange(state, this);
  };

  VoiceRecorder.prototype._clearTick = function () {
    if (this._tickTimer) {
      clearInterval(this._tickTimer);
      this._tickTimer = null;
    }
  };

  VoiceRecorder.prototype._startTick = function () {
    var self = this;
    this._clearTick();
    this._tickTimer = setInterval(function () {
      if (self.state !== "recording") return;
      self.elapsedMs = Date.now() - self.startedAt - self._accumPaused;
      self.onTick(self.elapsedMs, formatDuration(self.elapsedMs));
    }, 200);
  };

  VoiceRecorder.prototype.requestPermission = function () {
    var self = this;
    if (!VoiceRecorder.isSupported()) {
      this._setState("unsupported");
      return Promise.reject(new Error("Recording is not supported in this browser."));
    }
    this._setState("requesting");
    return navigator.mediaDevices
      .getUserMedia({ audio: true, video: false })
      .then(function (stream) {
        self.stream = stream;
        self._setState("idle");
        return stream;
      })
      .catch(function (err) {
        self._setState("denied");
        self.onError(err);
        throw err;
      });
  };

  VoiceRecorder.prototype.start = function () {
    var self = this;
    var ensure = this.stream
      ? Promise.resolve(this.stream)
      : this.requestPermission();
    return ensure.then(function (stream) {
      self.chunks = [];
      self.blob = null;
      self.elapsedMs = 0;
      self._accumPaused = 0;
      self._pausedAt = 0;
      var mime = self.getMimeType();
      var opts = mime ? { mimeType: mime } : undefined;
      try {
        self.mediaRecorder = opts
          ? new MediaRecorder(stream, opts)
          : new MediaRecorder(stream);
      } catch (err) {
        self.onError(err);
        throw err;
      }
      self.mimeType = self.mediaRecorder.mimeType || mime || "audio/webm";
      self.mediaRecorder.ondataavailable = function (ev) {
        if (ev.data && ev.data.size > 0) self.chunks.push(ev.data);
      };
      self.mediaRecorder.onerror = function (ev) {
        self.onError(ev.error || new Error("MediaRecorder error"));
      };
      self.mediaRecorder.start(250);
      self.startedAt = Date.now();
      self._setState("recording");
      self._startTick();
    });
  };

  VoiceRecorder.prototype.pause = function () {
    if (!this.mediaRecorder || this.state !== "recording") return;
    if (typeof this.mediaRecorder.pause === "function") {
      this.mediaRecorder.pause();
      this._pausedAt = Date.now();
      this._setState("paused");
      this._clearTick();
    }
  };

  VoiceRecorder.prototype.resume = function () {
    if (!this.mediaRecorder || this.state !== "paused") return;
    if (typeof this.mediaRecorder.resume === "function") {
      if (this._pausedAt) {
        this._accumPaused += Date.now() - this._pausedAt;
        this._pausedAt = 0;
      }
      this.mediaRecorder.resume();
      this._setState("recording");
      this._startTick();
    }
  };

  VoiceRecorder.prototype.stop = function () {
    var self = this;
    return new Promise(function (resolve, reject) {
      if (!self.mediaRecorder || (self.state !== "recording" && self.state !== "paused")) {
        resolve(self.blob);
        return;
      }
      self.mediaRecorder.onstop = function () {
        self._clearTick();
        self.elapsedMs = Date.now() - self.startedAt - self._accumPaused;
        self.blob = new Blob(self.chunks, {
          type: self.mimeType || "audio/webm",
        });
        self._setState("stopped");
        self.onTick(self.elapsedMs, formatDuration(self.elapsedMs));
        resolve(self.blob);
      };
      try {
        if (self.state === "paused" && typeof self.mediaRecorder.resume === "function") {
          self.mediaRecorder.resume();
        }
        self.mediaRecorder.stop();
      } catch (err) {
        reject(err);
      }
    });
  };

  VoiceRecorder.prototype.deleteRecording = function () {
    this.chunks = [];
    this.blob = null;
    this.elapsedMs = 0;
    this._accumPaused = 0;
    this._clearTick();
    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      try {
        this.mediaRecorder.stop();
      } catch (_e) {
        /* ignore */
      }
    }
    this.mediaRecorder = null;
    this._setState(this.stream ? "idle" : "idle");
  };

  VoiceRecorder.prototype.recordAgain = function () {
    this.deleteRecording();
    return this.start();
  };

  VoiceRecorder.prototype.release = function () {
    this.deleteRecording();
    if (this.stream) {
      this.stream.getTracks().forEach(function (t) {
        t.stop();
      });
      this.stream = null;
    }
  };

  VoiceRecorder.prototype.buildFile = function (blob, filename) {
    var b = blob || this.blob;
    if (!b) return null;
    var mime = b.type || this.mimeType || "audio/webm";
    var name =
      filename ||
      "recording-" +
        new Date().toISOString().replace(/[:.]/g, "-") +
        "." +
        extensionForMime(mime);
    return new File([b], name, { type: mime, lastModified: Date.now() });
  };

  VoiceRecorder.formatDuration = formatDuration;
  VoiceRecorder.pickMimeType = pickMimeType;
  VoiceRecorder.extensionForMime = extensionForMime;

  NS.VoiceRecorder = VoiceRecorder;
})(typeof window !== "undefined" ? window : this);
