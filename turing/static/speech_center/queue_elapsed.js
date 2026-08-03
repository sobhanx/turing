/**
 * Lightweight relative elapsed-time updates for Processing Queue cards.
 * Does not poll the server — only refreshes local "status — N minutes" text.
 */
(function () {
  var FORMS = {
    en: {
      second: ["%(n)d second", "%(n)d seconds"],
      minute: ["%(n)d minute", "%(n)d minutes"],
      hour: ["%(n)d hour", "%(n)d hours"],
      mixed: "%(hours)d h %(minutes)d min",
      active: "%(status)s — %(elapsed)s",
      total: "%(status)s — %(elapsed)s total",
    },
    fa: {
      second: ["%(n)d ثانیه", "%(n)d ثانیه"],
      minute: ["%(n)d دقیقه", "%(n)d دقیقه"],
      hour: ["%(n)d ساعت", "%(n)d ساعت"],
      mixed: "%(hours)d س %(minutes)d د",
      active: "%(status)s — %(elapsed)s",
      total: "%(status)s — مجموعاً %(elapsed)s",
    },
  };

  function forms() {
    var lang = (document.documentElement.lang || "en").toLowerCase();
    return FORMS[lang] || FORMS.en;
  }

  function fill(template, values) {
    return template.replace(/%\((\w+)\)d/g, function (_, key) {
      return String(values[key]);
    }).replace(/%\((\w+)\)s/g, function (_, key) {
      return String(values[key]);
    });
  }

  function plural(n, pair) {
    return fill(n === 1 ? pair[0] : pair[1], { n: n });
  }

  function formatElapsed(totalSeconds) {
    var f = forms();
    var total = Math.max(0, Math.floor(totalSeconds));
    if (total < 60) {
      return plural(total, f.second);
    }
    var minutes = Math.floor(total / 60);
    if (minutes < 60) {
      return plural(minutes, f.minute);
    }
    var hours = Math.floor(minutes / 60);
    var rem = minutes % 60;
    if (rem === 0) {
      return plural(hours, f.hour);
    }
    return fill(f.mixed, { hours: hours, minutes: rem });
  }

  function tick() {
    var f = forms();
    var nodes = document.querySelectorAll(".sc-queue-elapsed[data-sc-elapsed-since]");
    var now = Date.now();
    nodes.forEach(function (el) {
      if (el.getAttribute("data-sc-elapsed-terminal") === "1") return;
      var since = el.getAttribute("data-sc-elapsed-since");
      var label = el.getAttribute("data-sc-status-label") || "";
      if (!since) return;
      var start = Date.parse(since);
      if (!start) return;
      var elapsed = formatElapsed((now - start) / 1000);
      el.textContent = label
        ? fill(f.active, { status: label, elapsed: elapsed })
        : elapsed;
    });
  }

  tick();
  setInterval(tick, 15000);
})();
