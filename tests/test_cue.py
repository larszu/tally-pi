"""Tests fuer den Cue an die Buehne (Bedarf 104).

Der Bedarf steht so in der Recherche:

  > Speakers cannot read a cue sheet and are not on talkback. Messages are
  > passed by A PERSON WALKING, or by hand signals from the wings.

Belegt an `cpvalente/ontime#371` (2023-04-30): „Speaker are very busy and
stressed on stage and wish strong visual helpers on the active speech. This is
one piece of few wishes/requests from our collection what speakers told us
after a event was happen."

Der gefaehrlichste Zustand dieser Seite ist NICHT der leere, sondern der ALTE:
„Noch 2 Minuten", zehn Minuten spaeter unveraendert. Der Redner richtet sich
danach. Diese Datei prueft die reinen Teile -- die Annahme einer Nachricht, ihr
Ablaufen und den ausgelieferten Seitentext.

Lauf: `python3 -m unittest discover -s tests`.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_server as gs  # noqa: E402


class NachrichtAnnehmen(unittest.TestCase):
    """`normalise_cue` -- streng, weil eine halbe Nachricht schlimmer ist."""

    def test_nimmt_die_gewoehnliche_nachricht(self):
        c = gs.normalise_cue({"text": "Noch 5 Minuten"}, 1000.0)
        self.assertEqual(c["text"], "Noch 5 Minuten")
        self.assertEqual(c["kind"], "info")
        self.assertEqual(c["ttl_s"], gs.CUE_DEFAULT_TTL_S)
        self.assertEqual(c["at"], 1000.0)

    def test_verlangt_einen_text(self):
        # Eine leere Nachricht auf einem Schirm im Licht ist schlimmer als
        # keine: der Redner sucht nach etwas, das nicht da ist.
        for schlecht in ({}, {"text": ""}, {"text": "   "}, {"text": 5}):
            with self.assertRaises(ValueError):
                gs.normalise_cue(schlecht, 1000.0)

    def test_lehnt_eine_erfundene_dringlichkeit_ab(self):
        with self.assertRaises(ValueError):
            gs.normalise_cue({"text": "x", "kind": "panik"}, 1000.0)

    def test_faltet_umbrueche_und_kuerzt(self):
        # Was laenger ist, liest im Reden niemand -- und ein Umbruch zerreisst
        # die eine Zeile, um die es geht.
        c = gs.normalise_cue({"text": "a\n  b\tc"}, 1000.0)
        self.assertEqual(c["text"], "a b c")
        lang = gs.normalise_cue({"text": "x" * 500}, 1000.0)
        self.assertEqual(len(lang["text"]), gs.CUE_MAX_CHARS)

    def test_begrenzt_die_lebensdauer(self):
        # Eine Nachricht, die eine Stunde stehen bleibt, ist keine Nachricht
        # mehr, sondern eine Behauptung ueber die Gegenwart.
        with self.assertRaises(ValueError):
            gs.normalise_cue({"text": "x", "ttl_s": gs.CUE_MAX_TTL_S + 1}, 1000.0)
        with self.assertRaises(ValueError):
            gs.normalise_cue({"text": "x", "ttl_s": 0}, 1000.0)
        with self.assertRaises(ValueError):
            gs.normalise_cue({"text": "x", "ttl_s": True}, 1000.0)
        self.assertEqual(gs.normalise_cue({"text": "x", "ttl_s": 30}, 1000.0)["ttl_s"], 30)

    def test_lehnt_ab_was_kein_objekt_ist(self):
        for schlecht in ([], "text", 5, None):
            with self.assertRaises(ValueError):
                gs.normalise_cue(schlecht, 1000.0)


class NachrichtLaeuftAb(unittest.TestCase):
    """`cue_view` -- der eigentliche Punkt dieses Bedarfs."""

    def gueltig(self, **over):
        c = {"text": "Noch 5 Minuten", "kind": "wrap", "ttl_s": 120, "at": 1000.0}
        c.update(over)
        return c

    def test_zeigt_die_frische_nachricht(self):
        v = gs.cue_view(self.gueltig(), 1010.0)
        self.assertEqual(v["state"], "cue")
        self.assertEqual(v["text"], "Noch 5 Minuten")
        self.assertEqual(v["kind"], "wrap")
        self.assertEqual(v["age_s"], 10)

    def test_raeumt_die_abgelaufene_weg(self):
        # DER Fehler, um den es geht. Nicht „blass anzeigen", nicht „mit
        # Zeitstempel daneben" -- WEG. Der Redner liest sonst weiter, was
        # nicht mehr gilt.
        v = gs.cue_view(self.gueltig(), 1000.0 + 120)
        self.assertEqual(v["state"], "none")
        self.assertEqual(v["text"], "")

    def test_laeuft_genau_an_der_grenze_ab(self):
        self.assertEqual(gs.cue_view(self.gueltig(), 1119.9)["state"], "cue")
        self.assertEqual(gs.cue_view(self.gueltig(), 1120.0)["state"], "none")

    def test_ohne_zeitpunkt_gilt_sie_nicht(self):
        # „Gilt vermutlich noch" ist genau die Annahme, die hier verboten ist.
        for kaputt in ({"text": "x"}, {"text": "x", "at": "gestern"}, {"text": "x", "at": True}):
            self.assertEqual(gs.cue_view(kaputt, 1000.0)["state"], "none")

    def test_leerer_zustand_ist_kein_cue(self):
        for leer in ({}, None, [], {"text": ""}):
            self.assertEqual(gs.cue_view(leer, 1000.0)["state"], "none")

    def test_faellt_bei_kaputter_lebensdauer_auf_die_vorgabe(self):
        # Und NICHT auf „unbegrenzt". Eine kaputte Zahl darf die Nachricht
        # nicht unsterblich machen.
        c = self.gueltig(ttl_s="lange")
        self.assertEqual(gs.cue_view(c, 1000.0 + gs.CUE_DEFAULT_TTL_S)["state"], "none")
        c2 = self.gueltig(ttl_s=0)
        self.assertEqual(gs.cue_view(c2, 1000.0 + gs.CUE_DEFAULT_TTL_S)["state"], "none")

    def test_macht_aus_einer_erfundenen_dringlichkeit_info(self):
        self.assertEqual(gs.cue_view(self.gueltig(kind="panik"), 1010.0)["kind"], "info")

    def test_zaehlt_das_alter_nicht_negativ(self):
        # Eine Uhr, die zurueckspringt (NTP nach dem Booten), darf kein
        # negatives Alter erzeugen.
        self.assertEqual(gs.cue_view(self.gueltig(), 900.0)["age_s"], 0)


class DieBuehnenSeite(unittest.TestCase):
    """Der ausgelieferte Text -- ohne Netz, ohne Handler."""

    def setUp(self):
        self.seite = gs.render_cue_page()

    def test_hat_kein_bedienelement(self):
        # Der Empfaenger steht im Licht und redet. Alles Anfassbare ist ein
        # Ding, das er versehentlich trifft -- deshalb ist die Tally-Seite mit
        # ihrem Ton-Knopf hier ausdruecklich KEINE Vorlage.
        self.assertNotIn("<button", self.seite)
        self.assertNotIn("<input", self.seite)
        self.assertNotIn("<select", self.seite)
        self.assertNotIn("<a ", self.seite)

    def test_sagt_die_dringlichkeit_auch_in_worten(self):
        # Farbe allein traegt die Auskunft nicht: ein Teil der Leute
        # unterscheidet Rot und Orange nicht, und ein Beamer noch weniger.
        self.assertIn("ZUM SCHLUSS KOMMEN", self.seite)
        self.assertIn("BITTE JETZT BEENDEN", self.seite)

    def test_unterscheidet_keine_verbindung_von_nichts_anliegend(self):
        # Sonst haelt der Redner eine tote Leitung fuer Ruhe -- derselbe
        # Fehler, den Bedarf 86 auf der Tally-Seite abgestellt hat.
        self.assertIn("KEINE VERBINDUNG ZUR REGIE", self.seite)
        self.assertIn("#bg.stale", self.seite)
        self.assertIn("#bg.none", self.seite)

    def test_hat_einen_wachhund_mit_derselben_zahl_wie_das_tally(self):
        # Zwei getrennte Zahlen hiessen zwei Wahrheiten darueber, wann eine
        # Leitung als tot gilt.
        self.assertIn("var STALE_MS=%d" % int(gs.TALLY_STALE_S * 1000), self.seite)
        self.assertNotIn("__STALE_MS__", self.seite)
        self.assertIn("setInterval", self.seite)

    def test_raeumt_den_text_weg_wenn_die_leitung_stirbt(self):
        block = self.seite[self.seite.index("function veraltet"):self.seite.index("var es=")]
        self.assertIn("text.textContent=''", block)

    def test_zeigt_bei_nichts_anliegend_gar_nichts(self):
        # Der Redner soll nichts lesen muessen, um zu wissen, dass nichts
        # anliegt.
        block = self.seite[self.seite.index("function zeige"):self.seite.index("function veraltet")]
        self.assertIn("bg.className='none'", block)


class DieRegieSeite(unittest.TestCase):
    def setUp(self):
        self.seite = gs.render_cue_control_page()

    def test_hat_die_drei_handzeichen_als_knoepfe(self):
        for kind in gs.CUE_KINDS:
            self.assertIn('data-kind="%s"' % kind, self.seite)

    def test_sagt_dass_es_keine_empfangsbestaetigung_gibt(self):
        # Eine erfundene Bestaetigung („angezeigt" statt „gelesen") waere
        # schlimmer als keine: die Regie glaubte, die Nachricht sei angekommen.
        self.assertIn("KEINE Empfangsbestaetigung", self.seite)

    def test_setzt_die_zahlen_ein_statt_sie_zu_behaupten(self):
        self.assertIn(str(gs.CUE_DEFAULT_TTL_S), self.seite)
        self.assertIn('maxlength="%d"' % gs.CUE_MAX_CHARS, self.seite)
        self.assertNotIn("__TTL__", self.seite)
        self.assertNotIn("__MAXCHARS__", self.seite)


class DerZustandUeberlebtDenNeustartNicht(unittest.TestCase):
    def test_liegt_unter_run_und_nicht_unter_opt(self):
        # Ein Cue von gestern Abend, der nach dem Booten wieder auf dem Schirm
        # steht, ist genau die Luege, gegen die Bedarf 86 die Tally-Seite
        # abgedichtet hat.
        self.assertTrue(str(gs.CUE_FILE).startswith("/run/"))

    def test_ein_fehlender_zustand_ist_kein_fehler(self):
        # `load_cue` faellt auf {} zurueck; `cue_view({})` ist „nichts
        # anliegend". Zusammen: ein frisch gebooteter Pi zeigt einen leeren
        # Schirm und keinen Absturz.
        self.assertEqual(gs.cue_view(gs.load_cue(), 1000.0)["state"], "none")


class DieRoute(unittest.TestCase):
    """Die Reihenfolge im Router -- ohne sie faengt /cue die Buehne ab."""

    def setUp(self):
        self.quelle = Path(gs.__file__).read_text()

    def test_display_kommt_vor_der_regie_seite(self):
        i_display = self.quelle.index('self.path.startswith("/cue/display")')
        i_regie = self.quelle.index('self.path == "/cue" or self.path.startswith("/cue?")')
        self.assertLess(i_display, i_regie)
        i_stream = self.quelle.index('self.path.startswith("/cue/stream")')
        self.assertLess(i_stream, i_regie)

    def test_der_strom_nutzt_dieselbe_sende_entscheidung_wie_das_tally(self):
        block = self.quelle[self.quelle.index("def _handle_cue_stream"):
                            self.quelle.index("def _handle_tally_state")]
        self.assertIn("tally_stream_should_send", block)

    def test_der_strom_vergleicht_nur_was_den_schirm_aendert(self):
        # Das Alter waechst jede Sekunde. Verglichen man es mit, gaebe es nie
        # einen unveraenderten Zustand und der Herzschlag waere sinnlos.
        block = self.quelle[self.quelle.index("def _handle_cue_stream"):
                            self.quelle.index("def _handle_tally_state")]
        self.assertIn('(view["state"], view["text"], view["kind"])', block)
        self.assertNotIn('view["age_s"])', block)


class DerServerAntwortetWirklich(unittest.TestCase):
    """Ein echter Durchlauf ueber HTTP -- und der Grund, warum es ihn gibt.

    Die reinen Tests oben waren gruen, waehrend `POST /cue` in Wahrheit eine
    400 lieferte: `log_event(kind, **fields)` traegt selbst einen Parameter
    `kind`, und der Aufruf schickte einen zweiten. Die Nachricht stand dann
    schon auf dem Schirm, die Regie sah einen Fehler -- und haette sie noch
    einmal geschickt. Genau die Sorte Luege, gegen die dieses Repo seit
    Bedarf 86 anschreibt; gefunden hat sie erst ein Rauchtest.
    """

    def setUp(self):
        import socketserver
        import tempfile
        import threading
        self.tmp = Path(tempfile.mkdtemp())
        self._alt_cue, self._alt_log = gs.CUE_FILE, gs.EVENT_LOG_FILE
        gs.CUE_FILE = self.tmp / "cue.json"
        gs.EVENT_LOG_FILE = self.tmp / "events.log"
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), gs.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        gs.CUE_FILE, gs.EVENT_LOG_FILE = self._alt_cue, self._alt_log

    def _post(self, pfad, obj=None):
        import json as _json
        import urllib.error
        import urllib.request
        daten = _json.dumps(obj).encode() if obj is not None else b""
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, pfad), data=daten,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, _json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read().decode())

    def _get(self, pfad):
        import json as _json
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, pfad), timeout=5) as r:
            return r.status, r.read().decode()

    def test_ein_gesendeter_cue_antwortet_mit_200(self):
        code, body = self._post("/cue", {"text": "Noch 5 Minuten", "kind": "wrap"})
        self.assertEqual(code, 200)
        self.assertEqual(body["state"], "cue")
        self.assertEqual(body["kind"], "wrap")

    def test_der_schirm_und_die_antwort_sagen_dasselbe(self):
        # Der Kern des gefundenen Fehlers: Schirm und Antwort liefen
        # auseinander.
        self._post("/cue", {"text": "Noch 5 Minuten", "kind": "wrap"})
        code, body = self._get("/cue/state")
        self.assertEqual(code, 200)
        self.assertIn("Noch 5 Minuten", body)

    def test_ein_ungueltiger_cue_antwortet_mit_400_und_aendert_nichts(self):
        self._post("/cue", {"text": "Noch 5 Minuten"})
        code, _ = self._post("/cue", {"text": "", "kind": "stop"})
        self.assertEqual(code, 400)
        self.assertIn("Noch 5 Minuten", self._get("/cue/state")[1])

    def test_leeren_raeumt_den_schirm(self):
        self._post("/cue", {"text": "Noch 5 Minuten"})
        code, body = self._post("/cue/clear", None)
        self.assertEqual(code, 200)
        self.assertEqual(body["state"], "none")

    def test_die_buehnenseite_kommt_unter_ihrem_eigenen_pfad(self):
        # Und nicht die Regie-Seite: /cue/display darf nicht von /cue
        # abgefangen werden.
        self.assertIn("KEINE VERBINDUNG ZUR REGIE", self._get("/cue/display")[1])
        self.assertIn("data-kind", self._get("/cue")[1])


class KeinZweitesKind(unittest.TestCase):
    def test_kein_aufrufer_uebergibt_log_event_ein_zweites_kind(self):
        # `log_event(kind, **fields)` -- wer ein Feld `kind` mitschickt,
        # bekommt einen TypeError, und zwar erst zur Laufzeit im Handler.
        quelle = Path(gs.__file__).read_text()
        treffer = re.findall(r"log_event\([^)]*\bkind=", quelle)
        self.assertEqual(treffer, [], "log_event() bekommt ein zweites 'kind': %s" % treffer)


if __name__ == "__main__":
    unittest.main()
