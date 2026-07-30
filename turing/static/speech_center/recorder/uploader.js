/**
 * Upload recorded audio through the existing Speech Center upload endpoint.
 * Same contract as the file form: multipart file + organization_id + CSRF.
 */
(function (global) {
  "use strict";

  var NS = (global.TuringSpeechCenter = global.TuringSpeechCenter || {});

  var STEPS = [
    "preparing",
    "uploading",
    "complete",
    "redirecting",
  ];

  function RecorderUploader(options) {
    this.uploadUrl = options.uploadUrl;
    this.redirectUrl = options.redirectUrl;
    this.csrfToken = options.csrfToken;
    this.maxUploadBytes = options.maxUploadBytes || 0;
    this.uploadSource = options.uploadSource || "recorder";
    this.onProgress = options.onProgress || function () {};
    this.onStep = options.onStep || function () {};
    this.onError = options.onError || function () {};
  }

  RecorderUploader.STEPS = STEPS;

  RecorderUploader.prototype.uploadFile = function (file, organizationId) {
    var self = this;
    if (!file) {
      return Promise.reject(new Error("No recording to upload."));
    }
    if (!organizationId) {
      return Promise.reject(new Error("Please select an organization."));
    }
    if (self.maxUploadBytes && file.size > self.maxUploadBytes) {
      return Promise.reject(
        new Error(
          "Recording exceeds max upload size of " +
            self.maxUploadBytes +
            " bytes (" +
            file.size +
            " bytes)."
        )
      );
    }

    self.onStep("preparing", 0);
    var formData = new FormData();
    formData.append("organization_id", organizationId);
    formData.append("file", file, file.name);
    formData.append("upload_source", self.uploadSource || "recorder");
    if (self.csrfToken) {
      formData.append("csrfmiddlewaretoken", self.csrfToken);
    }

    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", self.uploadUrl, true);
      xhr.withCredentials = true;
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      if (self.csrfToken) {
        xhr.setRequestHeader("X-CSRFToken", self.csrfToken);
      }

      xhr.upload.onprogress = function (ev) {
        if (!ev.lengthComputable) return;
        var pct = Math.round((ev.loaded / ev.total) * 100);
        self.onStep("uploading", pct);
        self.onProgress(pct, ev.loaded, ev.total);
      };

      xhr.onloadstart = function () {
        self.onStep("uploading", 0);
      };

      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 400) {
          self.onStep("complete", 100);
          var loc =
            xhr.responseURL && xhr.responseURL !== self.uploadUrl
              ? xhr.responseURL
              : self.redirectUrl;
          resolve({
            redirectUrl: loc || self.redirectUrl,
            status: xhr.status,
            filename: file.name,
          });
          return;
        }
        var msg = "Upload failed (" + xhr.status + ").";
        try {
          var text = xhr.responseText || "";
          if (text && text.length < 300) msg = text;
        } catch (_e) {
          /* ignore */
        }
        self.onError(msg);
        reject(new Error(msg));
      };

      xhr.onerror = function () {
        var err = new Error("Network error while uploading recording.");
        self.onError(err.message);
        reject(err);
      };

      xhr.send(formData);
    });
  };

  /**
   * Place a File into an existing <input type="file"> via DataTransfer
   * so a classic form submit can be used without XHR.
   */
  RecorderUploader.assignFileInput = function (input, file) {
    if (!input || !file || typeof DataTransfer === "undefined") return false;
    var dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    return input.files && input.files.length === 1;
  };

  NS.RecorderUploader = RecorderUploader;
})(typeof window !== "undefined" ? window : this);
