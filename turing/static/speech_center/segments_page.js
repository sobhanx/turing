/**
 * Transcript Segments — direct event wiring for audio sync.
 */
(function () {
  "use strict";

  function init() {
    var audio = document.getElementById("sc-segments-audio");
    var list = document.getElementById("sc-segments-list");
    var search = document.getElementById("sc-segments-search");
    var meta = document.getElementById("sc-segments-filter-meta");
    var emptyFilter = document.getElementById("sc-segments-empty-filter");

    var segments = Array.prototype.slice.call(
      document.querySelectorAll(".transcript-segment")
    );

    console.log("[segments-init]", {
      audio: !!audio,
      audioId: audio ? audio.id : null,
      src: audio ? audio.currentSrc || audio.getAttribute("src") : null,
      readyState: audio ? audio.readyState : null,
      segmentCount: segments.length,
    });

    if (!segments.length) return;

    // Precompute ranges once (no DOM scan on timeupdate).
    var ranges = segments.map(function (el, index) {
      var start = Number(el.getAttribute("data-start"));
      var end = Number(el.getAttribute("data-end"));
      if (!isFinite(start)) {
        start =
          (parseInt(el.getAttribute("data-start-ms") || "0", 10) || 0) / 1000;
      }
      if (!isFinite(end) || end < start) {
        end =
          (parseInt(el.getAttribute("data-end-ms") || "0", 10) || 0) / 1000;
        if (!isFinite(end) || end < start) end = start;
      }
      return {
        index: index,
        id: el.getAttribute("data-segment-id") || String(index),
        el: el,
        start: start,
        end: end,
      };
    });

    var activeIndex = -1;

    function setActive(index, opts) {
      opts = opts || {};
      if (index === activeIndex) return;
      if (activeIndex >= 0 && ranges[activeIndex]) {
        ranges[activeIndex].el.classList.remove("active", "is-active");
      }
      activeIndex = index;
      if (activeIndex < 0 || !ranges[activeIndex]) {
        console.log("[audio-sync]", {
          currentTime: audio ? audio.currentTime : null,
          activeSegment: null,
        });
        return;
      }
      var seg = ranges[activeIndex];
      seg.el.classList.add("active", "is-active");
      console.log("[audio-sync]", {
        currentTime: audio ? audio.currentTime : null,
        activeSegment: seg.id,
      });
      if (opts.scroll) {
        seg.el.scrollIntoView({
          behavior: opts.instant ? "auto" : "smooth",
          block: "nearest",
        });
      }
    }

    function findIndexForTime(t) {
      var n = ranges.length;
      var lo = 0;
      var hi = n - 1;
      var cand = -1;
      while (lo <= hi) {
        var mid = (lo + hi) >> 1;
        if (ranges[mid].start <= t) {
          cand = mid;
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }
      if (cand < 0) return -1;
      var seg = ranges[cand];
      if (seg.start <= t && t < seg.end) return cand;
      if (cand === n - 1 && seg.start <= t && t <= seg.end) return cand;
      return -1;
    }

    function syncFromAudio(opts) {
      if (!audio) return;
      opts = opts || {};
      var t = Number(audio.currentTime);
      if (!isFinite(t)) t = 0;
      var idx = findIndexForTime(t);
      if (idx !== activeIndex) {
        setActive(idx, {
          scroll: true,
          instant: !!opts.instant,
        });
        return;
      }
      if (idx >= 0 && opts.forceScroll) {
        ranges[idx].el.scrollIntoView({
          behavior: opts.instant ? "auto" : "smooth",
          block: "nearest",
        });
      }
    }

    function onSegmentActivate(seg) {
      if (!audio) {
        console.log("[segment-click]", {
          segment: seg.id,
          start: seg.start,
          error: "no-audio",
        });
        return;
      }
      setActive(seg.index, { scroll: true });
      try {
        audio.currentTime = seg.start;
      } catch (err) {
        console.log("[segment-click]", {
          segment: seg.id,
          start: seg.start,
          error: String(err),
        });
      }
      var playPromise = audio.play();
      if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(function () {});
      }
      console.log("[segment-click]", {
        segment: seg.id,
        start: seg.start,
        "audio.currentTime": audio.currentTime,
      });
    }

    // 1) Direct click listeners on every .transcript-segment
    ranges.forEach(function (seg) {
      seg.el.style.cursor = "pointer";
      seg.el.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest(".sc-segment-toggle")) {
          return;
        }
        ev.preventDefault();
        onSegmentActivate(seg);
      });
    });

    // Collapse/expand (does not interfere with seek clicks above).
    if (list) {
      list.addEventListener("click", function (ev) {
        var toggle = ev.target.closest
          ? ev.target.closest(".sc-segment-toggle")
          : null;
        if (!toggle) return;
        ev.preventDefault();
        ev.stopPropagation();
        var card = toggle.closest(".transcript-segment");
        if (!card) return;
        var collapsed = card.classList.toggle("is-collapsed");
        toggle.textContent = collapsed ? "Expand" : "Collapse";
      });
    }

    // 2) Audio listeners
    if (audio) {
      function onAudioReady() {
        console.log("[audio-ready]", {
          readyState: audio.readyState,
          duration: audio.duration,
          currentTime: audio.currentTime,
        });
        syncFromAudio({ instant: true });
      }

      audio.addEventListener("loadedmetadata", onAudioReady);
      audio.addEventListener("timeupdate", function () {
        syncFromAudio();
      });
      audio.addEventListener("seeked", function () {
        syncFromAudio({ scroll: true, forceScroll: true, instant: true });
      });

      if (audio.readyState >= 1) {
        onAudioReady();
      }
    }

    // 3) Search filter
    function applyFilter() {
      if (!search) return;
      var q = (search.value || "").trim().toLowerCase();
      var visible = 0;
      segments.forEach(function (item) {
        var hay = (
          item.getAttribute("data-search") ||
          item.textContent ||
          ""
        ).toLowerCase();
        var show = !q || hay.indexOf(q) !== -1;
        item.hidden = !show;
        item.classList.toggle("is-search-hidden", !show);
        if (show) visible += 1;
      });
      if (meta) {
        meta.textContent = q
          ? visible + " / " + segments.length
          : segments.length
            ? String(segments.length) + " segments"
            : "";
      }
      if (emptyFilter) emptyFilter.hidden = !(q && visible === 0);
      if (list) {
        list.hidden = !!(q && visible === 0);
      }
    }
    if (search) {
      search.addEventListener("input", applyFilter);
      applyFilter();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
