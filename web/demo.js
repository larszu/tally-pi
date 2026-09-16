/* ---------------------------------------------------------------------------
 * Die Vorfuehrung auf der Projektseite — der Server, den es dort nicht gibt.
 *
 * ─── WAS GEMELDET WURDE (Nutzer, 2026-09-12) ───────────────────────────────
 *
 *   „Die GitHub page zeigt quasi nur das readme. Das ist unnötig. Starte
 *    dort die gesamte Anwendung"
 *
 * ─── WAS DIESE DATEI IST ───────────────────────────────────────────────────
 *
 * Ein NACHSCHLAGEWERK, kein zweites Programm. Die Seiten, in die sie
 * eingehaengt wird, sind die echten (`setup-guide.html`, `render_tally_page`,
 * die Cue-Seiten). Sie fragen ihren Server nach `/atem`, `/tally-config`,
 * `/tally-diagnostics`, `/logs` und den drei SSE-Stroemen. Auf GitHub Pages
 * gibt es keinen Server, also beantwortet diese Datei die Anfragen — aus
 * `daten.json`, die `scripts/build-demo.py` beim Bauen von den ECHTEN
 * Python-Funktionen hat ausrechnen lassen.
 *
 * DIE REGEL, DIE HIER GILT: hier wird NICHTS ENTSCHIEDEN, hier wird
 * NACHGESCHLAGEN. Insbesondere die Frage „ist diese Kamera rot?" —
 * `guide_server.tally_state_for_device` hat sie fuer jede Kombination aus
 * Eingang, ME-Bus und beobachteten AUX-Ausgaengen beantwortet, und hier wird
 * ihre Antwort nur noch herausgesucht. Eine JavaScript-Fassung derselben
 * Entscheidung waere die zweite, die beim naechsten Feature niemand
 * nachzieht — und sie faerbt eine Lampe auf einer Buehne.
 *
 * Was NICHT in der Tabelle steht (eine Kombination ausserhalb des Rasters),
 * wird `unknown` und sagt das. Geraten wird nicht.
 *
 * ─── WAS DIE VORFUEHRUNG NICHT VORTAEUSCHT ─────────────────────────────────
 *
 * Kein Pi, kein Mischer, keine GPIO-Leitungen. Wo Hardware fehlt, steht der
 * Grund, den das Programm selbst nennt („gpiod not available", „kein
 * systemd"). Das Kopfband sagt es auf jeder Seite, und die Knoepfe, die auf
 * dem Pi etwas schalten wuerden, antworten hier mit demselben Fehler, den
 * ein Rechner ohne Hardware gaebe.
 * ------------------------------------------------------------------------ */
(function () {
  "use strict";

  var WURZEL = window.__demoWurzel || "./";
  var SEITE = window.__demoSeite || "";
  // Die Tally- und Cue-Seiten SIND eine Farbflaeche — das ist ihr ganzer
  // Zweck: ein Blick quer durch ein dunkles Studio. Ein Band, das dort ein
  // Drittel des Handy-Schirms einnimmt, macht genau die Seite kaputt, die es
  // vorfuehren soll. Dort steht es deshalb einzeilig und laesst sich
  // zuklappen; die Flaeche beginnt darunter und bleibt ganz.
  var KOMPAKT = !!window.__demoKompakt;

  // Die Adresse, unter der die Vorfuehrung liegt. Die Oberflaeche baut daraus
  // ihre Tally-Links und QR-Codes (siehe `baseUrl()` in setup-guide.html):
  // ohne das zeigten sie auf den Wurzelpfad der github.io-Adresse, wo nichts
  // liegt.
  var BASIS = new URL(WURZEL, location.href).href.replace(/\/+$/, "");
  window.__tallyBase = BASIS;

  var daten = null;
  var lage = null;             // die gewaehlte Lage aus daten.lagen
  var konfig = null;           // die Konfiguration, wie sie in DIESEM Tab steht
  var protokoll = [];          // was in DIESEM Tab wirklich passiert ist
  var horcher = [];            // offene SSE-Stroeme
  var cue = null;              // zuletzt gesendete Nachricht (Regie -> Buehne)

  var bereitP = fetch(WURZEL + "daten.json", { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      daten = d;
      lage = d.lagen[0];
      konfig = JSON.parse(JSON.stringify(lage.antworten["/tally-config"]));
      // KEIN „watcher start": hier laeuft kein Watcher. Was wirklich
      // geschehen ist, ist das Laden einer Lage — und genau das steht da.
      notiere("atem", { action: "lage geladen", lage: lage.id,
                        connected: !!lage.antworten["/atem"].connected,
                        quelle: "Vorfuehrung (vorberechnet)" });
      return d;
    });

  function jetzt() { return Date.now() / 1000; }

  function notiere(art, felder) {
    var e = { ts: jetzt(), kind: art, src: "demo" };
    for (var k in felder) if (felder.hasOwnProperty(k)) e[k] = felder[k];
    protokoll.push(e);
    if (protokoll.length > 500) protokoll.splice(0, protokoll.length - 500);
    return e;
  }

  // ── Zustand nachschlagen, nicht ausrechnen ───────────────────────────────
  function zustandVon(geraet) {
    if (!geraet || typeof geraet.input !== "number") return "unknown";
    var aux = (geraet.aux || []).slice().sort(function (a, b) { return a - b; });
    var k = geraet.input + "|" + (geraet.me || 1) + "|" + aux.join(",");
    var s = lage.zustaende[k];
    return s === undefined ? "unknown" : s;
  }

  function geraetMit(id) {
    var liste = (konfig && konfig.devices) || [];
    for (var i = 0; i < liste.length; i++) if (liste[i].id === id) return liste[i];
    // Ad-hoc: die Tally-Seite darf eine reine Eingangsnummer bekommen.
    if (/^\d+$/.test(String(id))) return { id: id, input: parseInt(id, 10), me: 1, aux: [] };
    return null;
  }

  function diagnose() {
    // Zusammengestellt, nicht entschieden: `state` kommt aus der Tabelle,
    // die Hardware-Spalten aus der vorberechneten Antwort (dort sind sie
    // leer, weil kein Rechner ohne Pi Pin-Pegel hat).
    var vor = lage.antworten["/tally-diagnostics"];
    var vorProId = {};
    (vor.devices || []).forEach(function (d) { vorProId[d.id] = d; });
    var devices = ((konfig && konfig.devices) || []).map(function (d) {
      var v = vorProId[d.id] || {};
      return {
        id: d.id, name: d.name, atem_input: d.input, atem_me: d.me || 1,
        state: zustandVon(d),
        out_gpio: d.out_gpio == null ? null : d.out_gpio,
        out_trigger: d.out_trigger || "pgm",
        out_active_high: !!d.out_active_high,
        sw_on: v.sw_on === undefined ? null : v.sw_on,
        pin_level: v.pin_level === undefined ? null : v.pin_level,
        kernel_on: v.kernel_on === undefined ? null : v.kernel_on,
        consistent: v.consistent === undefined ? null : v.consistent,
        latched: false
      };
    });
    return {
      atem: vor.atem, devices: devices,
      claimed_bcms: vor.claimed_bcms || [],
      tally_out_error: vor.tally_out_error
    };
  }

  // ── Die Antworten ────────────────────────────────────────────────────────
  function antwort(pfad, optionen) {
    var m = String(pfad).split("?");
    var weg = m[0];
    var frage = new URLSearchParams(m[1] || "");
    var methode = ((optionen && optionen.method) || "GET").toUpperCase();

    if (methode === "POST") return antwortPost(weg, frage, optionen);

    if (weg === "/tally-config") return konfig;
    if (weg === "/tally-diagnostics") return diagnose();
    if (weg === "/ipconfig") return daten.ipconfig;
    if (weg === "/logs") {
      var n = parseInt(frage.get("limit") || "200", 10) || 200;
      return { events: protokoll.slice(-n) };
    }
    if (weg === "/tally/state") {
      var id = frage.get("id");
      return { id: id, state: zustandVon(geraetMit(id)) };
    }
    if (weg === "/cue") return cueAnsicht();
    if (lage.antworten.hasOwnProperty(weg)) return lage.antworten[weg];
    return { error: "in der Vorfuehrung nicht bedient: " + weg };
  }

  function antwortPost(weg, frage, optionen) {
    var koerper = {};
    try { koerper = JSON.parse((optionen && optionen.body) || "{}"); } catch (e) { koerper = {}; }

    if (weg === "/tally-config") {
      // Der Server mischt den neuen Stand in den alten; hier steht der
      // vollstaendige Stand aus dem Formular, und er gilt fuer diesen Tab.
      // Gespeichert wird NICHTS: beim Neuladen steht wieder die Beispiel-Anlage da.
      konfig = koerper;
      notiere("tally", { action: "config-save", devices: (koerper.devices || []).length });
      melde();
      return konfig;
    }
    if (weg === "/cue") {
      cue = { text: String(koerper.text || ""), kind: koerper.kind || "info",
              at: jetzt(), ttl_s: koerper.ttl_s || daten.cue.ttl_s };
      cueSenden();
      notiere("tally", { action: "cue", cue_kind: cue.kind });
      return cueAnsicht();
    }
    if (weg === "/cue/clear") {
      cue = null; cueSenden();
      notiere("tally", { action: "cue-clear" });
      return { state: "none", text: "", kind: "info", age_s: 0 };
    }
    if (weg.indexOf("/tally-out/") === 0) {
      // Genau die Antwort, die ein Rechner ohne GPIO gibt — sie steht in der
      // vorberechneten `/tally-out`-Antwort und kommt von `TallyOutputs`.
      var grund = lage.antworten["/tally-out"].error ||
                  "gpiod not available (kein Pi)";
      notiere("tally-out", { action: weg.split("/").slice(2).join(" "), ok: false,
                             error: grund });
      melde();
      return { ok: false, error: grund };
    }
    if (weg === "/input-test") {
      var g = geraetMit(koerper.id);
      var trocken = koerper.dry_run !== false;
      notiere("input", { bcm: g && g.in_gpio, edge: koerper.edge || "falling",
                         label: (g && g.name) || koerper.id,
                         dry_run: trocken, ok: trocken,
                         error: trocken ? undefined
                                        : "atem-cmd nicht vorhanden (kein Watcher)" });
      melde();
      return trocken
        ? { ok: true, fired: false, dry_run: true, msg: "dry-run protokolliert" }
        : { ok: false, fired: false,
            error: "atem-cmd nicht vorhanden (atem_watcher laeuft nicht)" };
    }
    return { ok: false, error: "in der Vorfuehrung nicht bedient: " + weg };
  }

  // ── fetch ────────────────────────────────────────────────────────────────
  var echtesFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (eingabe, optionen) {
    var url = typeof eingabe === "string" ? eingabe
            : (eingabe && eingabe.url) ? eingabe.url : String(eingabe);
    if (url.charAt(0) !== "/") {
      return echtesFetch ? echtesFetch(eingabe, optionen)
                         : Promise.reject(new Error("kein fetch"));
    }
    return bereitP.then(function () {
      var koerper = JSON.stringify(antwort(url, optionen || {}));
      return new Response(koerper, {
        status: 200, headers: { "Content-Type": "application/json" }
      });
    });
  };

  // ── EventSource ──────────────────────────────────────────────────────────
  //
  // Der Takt folgt den beiden Zahlen, die auch der Server benutzt und die in
  // `daten.json` stehen: bei Zustandswechsel senden, sonst im Herzschlag.
  // Ohne den Herzschlag koennte der Wachhund der Seite eine tote Leitung
  // nicht von „nichts Neues" unterscheiden — das ist der ganze Sinn beider
  // Zahlen, und sie stehen deshalb an EINER Stelle.
  function FalscherStrom(url) {
    var self = this;
    this.url = url; this.readyState = 0;
    this.onmessage = null; this.onerror = null; this.onopen = null;
    this._hoerer = {};
    this._letzte = null; this._zuletzt = 0;
    horcher.push(this);
    bereitP.then(function () {
      self.readyState = 1;
      if (self.onopen) self.onopen({});
      self._takt = setInterval(function () { self._pruefe(); }, 200);
      self._pruefe();
    });
  }
  FalscherStrom.prototype.addEventListener = function (art, fn) {
    (this._hoerer[art] = this._hoerer[art] || []).push(fn);
  };
  FalscherStrom.prototype.removeEventListener = function (art, fn) {
    var l = this._hoerer[art] || [];
    var i = l.indexOf(fn); if (i >= 0) l.splice(i, 1);
  };
  FalscherStrom.prototype.close = function () {
    this.readyState = 2;
    clearInterval(this._takt);
    var i = horcher.indexOf(this); if (i >= 0) horcher.splice(i, 1);
  };
  FalscherStrom.prototype._sende = function (nutzlast) {
    var ev = { data: JSON.stringify(nutzlast), type: "message" };
    if (this.onmessage) this.onmessage(ev);
    (this._hoerer.message || []).forEach(function (fn) { fn(ev); });
  };
  FalscherStrom.prototype._pruefe = function () {
    var teile = this.url.split("?");
    var weg = teile[0];
    var frage = new URLSearchParams(teile[1] || "");
    var nutzlast = null, marke = null;
    if (weg === "/tally/stream") {
      var id = frage.get("id");
      var s = zustandVon(geraetMit(id));
      nutzlast = { id: id, state: s }; marke = s;
    } else if (weg === "/cue/stream") {
      nutzlast = cueAnsicht();
      marke = nutzlast.state + "|" + nutzlast.text + "|" + nutzlast.kind;
    } else if (weg === "/logs/stream") {
      var neu = protokoll.filter(function (e) { return e.ts > (this._zuletzt || 0); }, this);
      if (!neu.length) return;
      this._zuletzt = neu[neu.length - 1].ts;
      var self = this;
      neu.forEach(function (e) { self._sende(e); });
      return;
    } else { return; }
    var alt = Date.now() - (this._sendeZeit || 0);
    if (marke !== this._letzte || alt >= daten.tally.herzschlag_ms) {
      this._letzte = marke; this._sendeZeit = Date.now();
      this._sende(nutzlast);
    }
  };
  window.EventSource = FalscherStrom;

  // ── Cue: Regie und Buehne, zwei Tabs, ein Kanal ──────────────────────────
  // Ein Kanal fuer beides: die Nachricht der Regie UND die gewaehlte Lage.
  // Das zweite ist der Grund, warum die Vorfuehrung ueberhaupt etwas zeigt:
  // wer die Tally-Seite auf dem Handy offen hat und am Rechner die Lage
  // umstellt, sieht die Flaeche umspringen — so wie am echten Mischer.
  var kanal = null;
  try { kanal = new BroadcastChannel("tally-pi-demo"); } catch (e) { kanal = null; }
  if (kanal) {
    kanal.onmessage = function (ev) {
      var n = ev.data || {};
      if (n.art === "cue") { cue = n.cue; return; }
      if (n.art === "lage") {
        var gewaehlt = daten && daten.lagen.filter(function (l) { return l.id === n.lage; })[0];
        if (!gewaehlt || gewaehlt === lage) return;
        lage = gewaehlt;
        konfig = JSON.parse(JSON.stringify(gewaehlt.antworten["/tally-config"]));
        notiere("atem", { action: "lage (aus einem anderen Tab)", lage: lage.id,
                          connected: !!lage.antworten["/atem"].connected });
        melde();
      }
    };
  }
  function cueSenden() { if (kanal) kanal.postMessage({ art: "cue", cue: cue }); }
  function lageSenden() { if (kanal) kanal.postMessage({ art: "lage", lage: lage.id }); }
  function cueAnsicht() {
    if (!cue || !cue.text) return { state: "none", text: "", kind: "info", age_s: 0 };
    var alter = Math.max(0, Math.floor(jetzt() - cue.at));
    // Nachgeschlagen, nicht entschieden: die Tabelle kommt von `cue_view`.
    var tab = daten.cue_zustaende;
    var zustand = alter < tab.length ? tab[alter] : tab[tab.length - 1];
    if (zustand === "none") return { state: "none", text: "", kind: "info", age_s: alter };
    return { state: "cue", text: cue.text, kind: cue.kind, age_s: alter };
  }

  // ── Das Kopfband ─────────────────────────────────────────────────────────
  function melde() { /* wird vom Kopfband ersetzt, sobald es steht */ }

  function kopfband() {
    var leiste = document.createElement("div");
    leiste.className = "demo-band" + (KOMPAKT ? " demo-band-schmal" : "");
    leiste.innerHTML =
      '<div class="demo-zeile demo-kopf">' +
        '<span class="demo-marke">Vorführung</span>' +
        '<span class="demo-text"></span>' +
        (KOMPAKT ? '<button type="button" class="demo-klappe">mehr ▾</button>' : '') +
      '</div>' +
      '<div class="demo-faltung">' +
        '<div class="demo-zeile demo-lagen"></div>' +
        '<div class="demo-zeile demo-wege"></div>' +
        '<div class="demo-zeile demo-erklaerung"></div>' +
      '</div>';
    leiste.querySelector(".demo-text").innerHTML = KOMPAKT
      ? 'Echte ' + (SEITE || 'Seite') + ', aber <strong>kein Pi und kein ' +
        'Mischer.</strong> ' +
        '<span class="demo-lagenname"></span>'
      : 'Das ist die <strong>echte Oberfläche</strong> von Tally Pi, im Browser. ' +
        '<strong>Kein Pi, kein Mischer, nichts wird geschaltet.</strong> ' +
        'Die Zustände hat <code>guide_server.py</code> beim Bauen ausgerechnet.';
    document.body.insertBefore(leiste, document.body.firstChild);
    document.body.classList.add("demo-hat-band");
    if (KOMPAKT) {
      leiste.classList.add("zu");
      leiste.querySelector(".demo-klappe").addEventListener("click", function () {
        leiste.classList.toggle("zu");
        this.textContent = leiste.classList.contains("zu") ? "mehr ▾" : "weniger ▴";
        hoeheMelden();
      });
    }

    var lagenEl = leiste.querySelector(".demo-lagen");
    lagenEl.appendChild(beschriftung("Lage:"));
    daten.lagen.forEach(function (l) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "demo-knopf"; b.textContent = l.titel;
      b.addEventListener("click", function () {
        lage = l;
        konfig = JSON.parse(JSON.stringify(l.antworten["/tally-config"]));
        notiere("atem", { action: "lage", lage: l.id,
                          connected: !!l.antworten["/atem"].connected });
        lageSenden();
        melde();
      });
      lagenEl.appendChild(b);
    });

    var wegeEl = leiste.querySelector(".demo-wege");
    wegeEl.appendChild(beschriftung("Seiten:"));
    wegeEl.appendChild(weg(BASIS + "/index.html", "Setup-Oberfläche"));
    daten.geraete.forEach(function (g) {
      wegeEl.appendChild(weg(BASIS + "/tally/" + g.id + "/", "Tally: " + g.name));
    });
    wegeEl.appendChild(weg(BASIS + "/cue/", "Cue-Anzeige (Bühne)"));
    wegeEl.appendChild(weg(BASIS + "/cue/control/", "Cue-Regie"));
    wegeEl.appendChild(weg(new URL("../", BASIS + "/").href, "Zur Projektseite"));

    // Die Farbflaeche der Tally-Seite liegt fest ueber dem Fenster. Sie
    // beginnt unter dem Band — und wie hoch das ist, weiss erst der Browser,
    // der es gesetzt hat (auf einem schmalen Schirm bricht es um).
    function hoeheMelden() {
      document.documentElement.style.setProperty(
        "--demo-band-h", leiste.getBoundingClientRect().height + "px");
    }
    window.addEventListener("resize", hoeheMelden);

    melde = function () {
      var aktiv = leiste.querySelectorAll(".demo-lagen .demo-knopf");
      daten.lagen.forEach(function (l, i) {
        aktiv[i].classList.toggle("an", l.id === lage.id);
      });
      leiste.querySelector(".demo-erklaerung").textContent = lage.erklaerung;
      var name = leiste.querySelector(".demo-lagenname");
      if (name) name.textContent = "Lage: " + lage.titel;
      hoeheMelden();
    };
    melde();
    hoeheMelden();
  }

  function beschriftung(t) {
    var s = document.createElement("span");
    s.className = "demo-beschriftung"; s.textContent = t;
    return s;
  }
  function weg(href, text) {
    var a = document.createElement("a");
    a.className = "demo-weg"; a.href = href; a.textContent = text;
    return a;
  }

  function start() {
    bereitP.then(function () {
      kopfband();
      if (SEITE) document.title = document.title + " — Vorführung";
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else { start(); }
})();
