/**
 * Waveform preview + non-destructive trim handles (applied only on Save).
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
    this._dragging = null;
    this.onChange = this.options.onChange || function () {};
    this._bindPointer();
  }

  WaveformEditor.prototype._bindPointer = function () {
    var self = this;
    function posRatio(ev) {
      var rect = self.canvas.getBoundingClientRect();
      var x = (ev.clientX - rect.left) / Math.max(1, rect.width);
      return Math.min(1, Math.max(0, x));
    }
    function nearHandle(ratio, handle) {
      return Math.abs(ratio - handle) < 0.03;
    }
    this.canvas.addEventListener("pointerdown", function (ev) {
      if (!self.peaks.length) return;
      var r = posRatio(ev);
      if (nearHandle(r, self.trimStart)) self._dragging = "start";
      else if (nearHandle(r, self.trimEnd)) self._dragging = "end";
      else if (r < (self.trimStart + self.trimEnd) / 2) {
        self.trimStart = Math.min(r, self.trimEnd - 0.01);
        self._dragging = "start";
      } else {
        self.trimEnd = Math.max(r, self.trimStart + 0.01);
        self._dragging = "end";
      }
      self.canvas.setPointerCapture(ev.pointerId);
      self.draw();
      self.onChange(self.getTrim());
    });
    this.canvas.addEventListener("pointermove", function (ev) {
      if (!self._dragging) return;
      var r = posRatio(ev);
      if (self._dragging === "start") {
        self.trimStart = Math.min(r, self.trimEnd - 0.01);
      } else {
        self.trimEnd = Math.max(r, self.trimStart + 0.01);
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

  WaveformEditor.prototype.loadBlob = function (blob) {
    var self = this;
    if (!blob) {
      this.reset();
      return Promise.resolve(null);
    }
    var AudioCtx = global.AudioContext || global.webkitAudioContext;
    if (!AudioCtx) {
      this.peaks = [];
      this.durationSec = 0;
      this.draw();
      return Promise.reject(new Error("Web Audio API unavailable for waveform."));
    }
    return blob.arrayBuffer().then(function (buf) {
      var ctx = new AudioCtx();
      return ctx.decodeAudioData(buf.slice(0)).then(
        function (audioBuffer) {
          self.durationSec = audioBuffer.duration || 0;
          self.peaks = self._computePeaks(audioBuffer, 240);
          self.trimStart = 0;
          self.trimEnd = 1;
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
  };

  WaveformEditor.prototype._computePeaks = function (audioBuffer, bars) {
    var channel = audioBuffer.getChannelData(0);
    var block = Math.floor(channel.length / bars) || 1;
    var peaks = [];
    for (var i = 0; i < bars; i++) {
      var start = i * block;
      var end = Math.min(start + block, channel.length);
      var max = 0;
      for (var j = start; j < end; j++) {
        var v = Math.abs(channel[j]);
        if (v > max) max = v;
      }
      peaks.push(max);
    }
    return peaks;
  };

  WaveformEditor.prototype.draw = function () {
    var c = this.canvas;
    var ctx = this.ctx;
    var dpr = global.devicePixelRatio || 1;
    var cssW = c.clientWidth || 480;
    var cssH = c.clientHeight || 96;
    c.width = Math.floor(cssW * dpr);
    c.height = Math.floor(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    var styles = getComputedStyle(c);
    var fg = styles.getPropertyValue("--sc-accent").trim() || "#2563eb";
    var muted = styles.getPropertyValue("--sc-muted").trim() || "#9ca3af";
    var surface = styles.getPropertyValue("--sc-surface").trim() || "#fff";

    ctx.fillStyle = surface;
    ctx.fillRect(0, 0, cssW, cssH);

    var mid = cssH / 2;
    var n = this.peaks.length || 1;
    var gap = 1;
    var barW = Math.max(1, (cssW - gap * n) / n);
    for (var i = 0; i < this.peaks.length; i++) {
      var x = i * (barW + gap);
      var ratio = i / n;
      var inSel = ratio >= this.trimStart && ratio <= this.trimEnd;
      var h = Math.max(2, this.peaks[i] * (cssH * 0.85));
      ctx.fillStyle = inSel ? fg : muted;
      ctx.globalAlpha = inSel ? 0.85 : 0.35;
      ctx.fillRect(x, mid - h / 2, barW, h);
    }
    ctx.globalAlpha = 1;

    function drawHandle(ratio) {
      var x = ratio * cssW;
      ctx.strokeStyle = fg;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, cssH);
      ctx.stroke();
      ctx.fillStyle = fg;
      ctx.beginPath();
      ctx.arc(x, mid, 5, 0, Math.PI * 2);
      ctx.fill();
    }
    if (this.peaks.length) {
      drawHandle(this.trimStart);
      drawHandle(this.trimEnd);
    }
  };

  /**
   * Apply trim destructively only when exporting for upload.
   * Full-range returns the original blob (no transcoding).
   * Trimmed range renders to WAV via OfflineAudioContext.
   */
  WaveformEditor.prototype.exportBlob = function (sourceBlob) {
    var trim = this.getTrim();
    if (!sourceBlob) return Promise.resolve(null);
    if (trim.isFull || this.durationSec <= 0) {
      return Promise.resolve(sourceBlob);
    }
    var AudioCtx = global.AudioContext || global.webkitAudioContext;
    if (!AudioCtx) {
      return Promise.reject(new Error("Cannot trim without Web Audio API."));
    }
    var startSec = trim.startSec;
    var endSec = trim.endSec;
    return sourceBlob.arrayBuffer().then(function (buf) {
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
