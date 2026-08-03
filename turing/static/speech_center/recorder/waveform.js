/**
 * Waveform preview + non-destructive trim handles (applied only on export/upload).
 * Shared by Recording review and Upload File review.
 */
(function (global) {
  "use strict";

  var NS = (global.TuringSpeechCenter = global.TuringSpeechCenter || {});

  function WaveformEditor(canvas, options) {
    this.canvas = canvas;
    this.options = options || {};
    this.ctx = canvas.getContext("2d");
    this.peaks = [];
    this.durationSec = 0;
    this.trimStart = 0; // 0..1
    this.trimEnd = 1; // 0..1
    this.playheadSec = null;
    this._dragging = null;
    this._sourceBlob = null;
    this.onChange = this.options.onChange || function () {};
    this._bindPointer();
    this._bindResize();
  }

  WaveformEditor.prototype._bindResize = function () {
    var self = this;
    if (typeof ResizeObserver === "undefined") {
      global.addEventListener("resize", function () {
        if (self.peaks.length) self.draw();
      });
      return;
    }
    this._ro = new ResizeObserver(function () {
      if (self.peaks.length) self.draw();
    });
    this._ro.observe(this.canvas);
    if (this.canvas.parentElement) {
      this._ro.observe(this.canvas.parentElement);
    }
  };

  WaveformEditor.prototype._bindPointer = function () {
    var self = this;
    function posRatio(ev) {
      var rect = self.canvas.getBoundingClientRect();
      var x = (ev.clientX - rect.left) / Math.max(1, rect.width);
      return Math.min(1, Math.max(0, x));
    }
    function nearHandle(ratio, handle) {
      return Math.abs(ratio - handle) < 0.035;
    }
    this.canvas.addEventListener("pointerdown", function (ev) {
      if (!self.peaks.length) return;
      ev.preventDefault();
      var r = posRatio(ev);
      if (nearHandle(r, self.trimStart)) self._dragging = "start";
      else if (nearHandle(r, self.trimEnd)) self._dragging = "end";
      else if (r < (self.trimStart + self.trimEnd) / 2) {
        self.trimStart = Math.min(r, self.trimEnd - 0.005);
        self._dragging = "start";
      } else {
        self.trimEnd = Math.max(r, self.trimStart + 0.005);
        self._dragging = "end";
      }
      try {
        self.canvas.setPointerCapture(ev.pointerId);
      } catch (_e) {
        /* ignore */
      }
      self.draw();
      self.onChange(self.getTrim());
    });
    this.canvas.addEventListener("pointermove", function (ev) {
      if (!self._dragging) return;
      var r = posRatio(ev);
      if (self._dragging === "start") {
        self.trimStart = Math.min(r, self.trimEnd - 0.005);
      } else {
        self.trimEnd = Math.max(r, self.trimStart + 0.005);
      }
      self.draw();
      self.onChange(self.getTrim());
    });
    this.canvas.addEventListener("pointerup", function () {
      self._dragging = null;
    });
    this.canvas.addEventListener("pointercancel", function () {
      self._dragging = null;
    });
  };

  WaveformEditor.prototype.reset = function () {
    this.peaks = [];
    this.durationSec = 0;
    this.trimStart = 0;
    this.trimEnd = 1;
    this.playheadSec = null;
    this._sourceBlob = null;
    this.draw();
    this.onChange(this.getTrim());
  };

  WaveformEditor.prototype.getTrim = function () {
    return {
      startRatio: this.trimStart,
      endRatio: this.trimEnd,
      startSec: this.trimStart * this.durationSec,
      endSec: this.trimEnd * this.durationSec,
      durationSec: Math.max(0, (this.trimEnd - this.trimStart) * this.durationSec),
      isFull: this.trimStart <= 0.001 && this.trimEnd >= 0.999,
    };
  };

  WaveformEditor.prototype.setPlayhead = function (sec) {
    if (sec == null || !isFinite(sec)) {
      this.playheadSec = null;
    } else {
      this.playheadSec = Math.max(0, Math.min(this.durationSec || 0, sec));
    }
    this.draw();
  };

  /**
   * Wait until the canvas has a real layout size (panel may have been hidden).
   */
  WaveformEditor.prototype.whenVisible = function () {
    var self = this;
    return new Promise(function (resolve) {
      var tries = 0;
      function tick() {
        var w = self.canvas.clientWidth;
        var h = self.canvas.clientHeight;
        if ((w > 8 && h > 8) || tries > 40) {
          resolve({ width: w, height: h });
          return;
        }
        tries += 1;
        global.requestAnimationFrame(tick);
      }
      global.requestAnimationFrame(tick);
    });
  };

  WaveformEditor.prototype.loadBlob = function (blob) {
    var self = this;
    if (!blob) {
      this.reset();
      return Promise.resolve(null);
    }
    this._sourceBlob = blob;
    var AudioCtx = global.AudioContext || global.webkitAudioContext;
    if (!AudioCtx) {
      this.peaks = [];
      this.durationSec = 0;
      this.draw();
      return Promise.reject(new Error("Web Audio API unavailable for waveform."));
    }
    return this.whenVisible().then(function () {
      return blob.arrayBuffer().then(function (buf) {
        var ctx = new AudioCtx();
        return ctx.decodeAudioData(buf.slice(0)).then(
          function (audioBuffer) {
            self.durationSec = audioBuffer.duration || 0;
            var bars = self._barsForWidth();
            self.peaks = self._computePeaks(audioBuffer, bars);
            self.trimStart = 0;
            self.trimEnd = 1;
            self.playheadSec = null;
            self.draw();
            self.onChange(self.getTrim());
            if (typeof ctx.close === "function") ctx.close();
            return audioBuffer;
          },
          function (err) {
            if (typeof ctx.close === "function") ctx.close();
            throw err;
          }
        );
      });
    });
  };

  WaveformEditor.prototype._barsForWidth = function () {
    var cssW = this.canvas.clientWidth || 480;
    return Math.min(720, Math.max(180, Math.floor(cssW / 2)));
  };

  WaveformEditor.prototype._computePeaks = function (audioBuffer, bars) {
    var channel = audioBuffer.getChannelData(0);
    var block = Math.floor(channel.length / bars) || 1;
    // For large files, sample within each block instead of scanning every sample.
    var step = Math.max(1, Math.floor(block / 48));
    var peaks = [];
    for (var i = 0; i < bars; i++) {
      var start = i * block;
      var end = Math.min(start + block, channel.length);
      var max = 0;
      for (var j = start; j < end; j += step) {
        var v = Math.abs(channel[j]);
        if (v > max) max = v;
      }
      peaks.push(max);
    }
    return peaks;
  };

  WaveformEditor.prototype._formatTick = function (sec) {
    sec = Math.max(0, sec || 0);
    var total = Math.floor(sec);
    var m = Math.floor(total / 60);
    var s = total % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  };

  WaveformEditor.prototype.draw = function () {
    var c = this.canvas;
    var ctx = this.ctx;
    var dpr = global.devicePixelRatio || 1;
    var cssW = c.clientWidth || Number(c.getAttribute("width")) || 480;
    var cssH = c.clientHeight || Number(c.getAttribute("height")) || 96;
    if (cssW < 2 || cssH < 2) return;

    c.width = Math.floor(cssW * dpr);
    c.height = Math.floor(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    var styles = getComputedStyle(c);
    var fg = styles.getPropertyValue("--sc-accent").trim() || "#2563eb";
    var muted = styles.getPropertyValue("--sc-muted").trim() || "#9ca3af";
    var surface = styles.getPropertyValue("--sc-surface").trim() || "#fff";
    var border = styles.getPropertyValue("--sc-border").trim() || "#e5e7eb";

    var timelineH = 18;
    var waveH = Math.max(40, cssH - timelineH);
    var mid = waveH / 2;

    ctx.fillStyle = surface;
    ctx.fillRect(0, 0, cssW, cssH);

    // Waveform bars
    var n = this.peaks.length || 1;
    var gap = 1;
    var barW = Math.max(1, (cssW - gap * n) / n);
    for (var i = 0; i < this.peaks.length; i++) {
      var x = i * (barW + gap);
      var ratio = i / Math.max(1, n - 1);
      var inSel = ratio >= this.trimStart && ratio <= this.trimEnd;
      var h = Math.max(2, this.peaks[i] * (waveH * 0.82));
      ctx.fillStyle = inSel ? fg : muted;
      ctx.globalAlpha = inSel ? 0.9 : 0.32;
      ctx.fillRect(x, mid - h / 2, barW, h);
    }
    ctx.globalAlpha = 1;

    // Selection overlay edges
    if (this.peaks.length) {
      ctx.fillStyle = "rgba(0,0,0,0.08)";
      ctx.fillRect(0, 0, this.trimStart * cssW, waveH);
      ctx.fillRect(this.trimEnd * cssW, 0, cssW - this.trimEnd * cssW, waveH);
    }

    function drawHandle(ratio) {
      var hx = ratio * cssW;
      ctx.strokeStyle = fg;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(hx, 0);
      ctx.lineTo(hx, waveH);
      ctx.stroke();
      ctx.fillStyle = fg;
      ctx.beginPath();
      ctx.arc(hx, mid, 6, 0, Math.PI * 2);
      ctx.fill();
      // Grab cue
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.arc(hx, mid, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
    if (this.peaks.length) {
      drawHandle(this.trimStart);
      drawHandle(this.trimEnd);
    }

    // Playhead
    if (
      this.playheadSec != null &&
      this.durationSec > 0 &&
      this.peaks.length
    ) {
      var pr = this.playheadSec / this.durationSec;
      var px = pr * cssW;
      ctx.strokeStyle = "#dc2626";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, waveH);
      ctx.stroke();
    }

    // Timeline markers
    ctx.fillStyle = border;
    ctx.fillRect(0, waveH, cssW, 1);
    ctx.fillStyle = muted;
    ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
    ctx.textBaseline = "top";
    var dur = this.durationSec || 0;
    if (dur > 0) {
      var tickCount = cssW > 520 ? 8 : cssW > 360 ? 6 : 4;
      for (var t = 0; t <= tickCount; t++) {
        var tr = t / tickCount;
        var tx = tr * cssW;
        var label = this._formatTick(tr * dur);
        ctx.fillStyle = border;
        ctx.fillRect(tx, waveH, 1, 4);
        ctx.fillStyle = muted;
        if (t === tickCount) {
          ctx.textAlign = "right";
          ctx.fillText(label, Math.min(cssW - 2, tx), waveH + 5);
        } else if (t === 0) {
          ctx.textAlign = "left";
          ctx.fillText(label, Math.max(2, tx), waveH + 5);
        } else {
          ctx.textAlign = "center";
          ctx.fillText(label, tx, waveH + 5);
        }
      }
    }
  };

  /**
   * Apply trim destructively only when exporting for upload.
   * Full-range returns the original blob (no transcoding).
   * Trimmed range renders to WAV via OfflineAudioContext.
   */
  WaveformEditor.prototype.exportBlob = function (sourceBlob) {
    var trim = this.getTrim();
    var blob = sourceBlob || this._sourceBlob;
    if (!blob) return Promise.resolve(null);
    if (trim.isFull || this.durationSec <= 0) {
      return Promise.resolve(blob);
    }
    var AudioCtx = global.AudioContext || global.webkitAudioContext;
    if (!AudioCtx) {
      return Promise.reject(new Error("Cannot trim without Web Audio API."));
    }
    var startSec = trim.startSec;
    var endSec = trim.endSec;
    return blob.arrayBuffer().then(function (buf) {
      var ctx = new AudioCtx();
      return ctx.decodeAudioData(buf.slice(0)).then(function (audioBuffer) {
        var sampleRate = audioBuffer.sampleRate;
        var startFrame = Math.floor(startSec * sampleRate);
        var endFrame = Math.min(
          audioBuffer.length,
          Math.floor(endSec * sampleRate)
        );
        var frameCount = Math.max(1, endFrame - startFrame);
        var offline = new OfflineAudioContext(
          audioBuffer.numberOfChannels,
          frameCount,
          sampleRate
        );
        var tmp = offline.createBuffer(
          audioBuffer.numberOfChannels,
          frameCount,
          sampleRate
        );
        for (var ch = 0; ch < audioBuffer.numberOfChannels; ch++) {
          var src = audioBuffer.getChannelData(ch).subarray(startFrame, endFrame);
          tmp.copyToChannel(src, ch);
        }
        var source = offline.createBufferSource();
        source.buffer = tmp;
        source.connect(offline.destination);
        source.start(0);
        return offline.startRendering().then(function (rendered) {
          if (typeof ctx.close === "function") ctx.close();
          return audioBufferToWavBlob(rendered);
        });
      });
    });
  };

  function audioBufferToWavBlob(buffer) {
    var numChannels = buffer.numberOfChannels;
    var sampleRate = buffer.sampleRate;
    var samples = buffer.length;
    var bytesPerSample = 2;
    var blockAlign = numChannels * bytesPerSample;
    var dataSize = samples * blockAlign;
    var headerSize = 44;
    var arrayBuffer = new ArrayBuffer(headerSize + dataSize);
    var view = new DataView(arrayBuffer);

    function writeStr(offset, str) {
      for (var i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    }
    writeStr(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * blockAlign, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);
    writeStr(36, "data");
    view.setUint32(40, dataSize, true);

    var offset = 44;
    var channels = [];
    for (var c = 0; c < numChannels; c++) channels.push(buffer.getChannelData(c));
    for (var i = 0; i < samples; i++) {
      for (var ch = 0; ch < numChannels; ch++) {
        var sample = Math.max(-1, Math.min(1, channels[ch][i]));
        view.setInt16(
          offset,
          sample < 0 ? sample * 0x8000 : sample * 0x7fff,
          true
        );
        offset += 2;
      }
    }
    return new Blob([arrayBuffer], { type: "audio/wav" });
  }

  NS.WaveformEditor = WaveformEditor;
})(typeof window !== "undefined" ? window : this);
