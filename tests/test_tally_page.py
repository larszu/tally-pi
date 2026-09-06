"""Tests fuer die Tally-Seite eines entfernten Beitragenden (Bedarf 86).

Der Bedarf ist fuenf Jahre alt und in zwei offenen Fremd-Issues belegt:

  > Remote guests have no reliable way to know they are live; the in-room
  > tally chain is solved and stops at the venue boundary.

steveseguin/vdo.ninja#569 (Tally im Director-View, 2020) und #654 (On-Air-
Anzeige und HOERBARER Alarm fuer Gaeste, 2021).

`tests/test_tally_state.py` haelt fuer die SERVER-Funktion ausdruecklich fest:
„Ein unbekannter Zustand darf nicht wie 'sicher' aussehen." Fuer die SEITE galt
bis 2026-09-06 das Gegenteil, und zwar dreifach:

1. `offline` war `#1e293b` neben `safe` `#111` — auf einem Handy im Tageslicht
   nicht unterscheidbar. Der Gast entspannt sich, weil der Schirm dunkel ist.
2. Ein leerer Zustand fiel per `d.state||'safe'` auf die BERUHIGENDE Antwort.
3. Der Herzschlag des Streams war ein SSE-KOMMENTAR (`: ping`) und damit fuer
   `onmessage` unsichtbar; einen Wachhund gab es nicht. Eine halboffene
   Verbindung durch ein NAT — die Normallage einer Fernstrecke — liefert
   nichts mehr und feuert kein `onerror`. Die Seite zeigte den letzten Zustand
   weiter, unbegrenzt.

Diese Datei prueft die reinen Teile: die Sende-Entscheidung des Streams und
den ausgelieferten Seiten-Text. Lauf: `python3 -m unittest discover -s tests`.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_server as gs  # noqa: E402


class SendeEntscheidung(unittest.TestCase):
    """`tally_stream_should_send` — der Herzschlag, den die Seite sehen kann."""

    def test_erster_versand_immer(self):
        # Ohne das wartet ein frisch verbundener Gast bis zum naechsten
        # Schnitt auf seine erste Auskunft.
        self.assertTrue(gs.tally_stream_should_send("safe", None, 0.0))

    def test_zustandswechsel_sofort(self):
        self.assertTrue(gs.tally_stream_should_send("pgm", "safe", 0.0))

    def test_unveraendert_und_frisch_schweigt(self):
        self.assertFalse(gs.tally_stream_should_send("safe", "safe", 0.5))

    def test_unveraendert_aber_alt_sendet_trotzdem(self):
        # DAS ist der neue Grund. Ohne ihn kann die Seite eine tote Leitung
        # nicht von einem ruhigen Mischer unterscheiden.
        self.assertTrue(
            gs.tally_stream_should_send("safe", "safe", gs.TALLY_HEARTBEAT_S))
        self.assertTrue(
            gs.tally_stream_should_send("safe", "safe", gs.TALLY_HEARTBEAT_S + 5))

    def test_wachhund_wartet_laenger_als_zwei_herzschlaege(self):
        # Ein einzelner verlorener Herzschlag ueber Mobilfunk ist normal,
        # zwei sind ein Befund. Waere die Frist kuerzer, schluege der
        # Wachhund im Normalbetrieb an -- und wer ihn dreimal grundlos
        # sieht, glaubt ihm beim vierten Mal nicht mehr.
        self.assertGreater(gs.TALLY_STALE_S, 2 * gs.TALLY_HEARTBEAT_S)

    def test_kein_kommentar_heartbeat_mehr_im_tally_stream(self):
        # NUR der Tally-Stream. `: ping` ist ein SSE-KOMMENTAR: er haelt
        # Proxys wach und ist fuer `onmessage` unsichtbar -- genau daran lag
        # der Befund. Im LOG-Stream ist er weiter richtig; dort haengt keine
        # Sicherheitsauskunft daran, und eine Zusicherung ueber die ganze
        # Datei haette ihn mitgerissen, ohne dass jemand gefragt haette.
        import inspect
        quelle = inspect.getsource(gs.Handler._handle_tally_stream)
        # Auf den SCHREIB-Aufruf pruefen, nicht auf die Zeichenfolge: der
        # Kommentar ueber der Schleife nennt den alten Herzschlag beim Namen,
        # und darauf zu pruefen hiesse, die Begruendung zu verbieten.
        self.assertNotIn('wfile.write(b": ping', quelle)
        self.assertIn("tally_stream_should_send", quelle)


class SeitenText(unittest.TestCase):
    """Der ausgelieferte HTML-Text — was der Gast wirklich sieht."""

    def setUp(self):
        self.html = gs.render_tally_page("cam1", "Kamera 1")

    def test_keine_platzhalter_bleiben_stehen(self):
        for ph in ("__ID__", "__NAME__", "__STALE_MS__", "__HEARTBEAT_MS__"):
            self.assertNotIn(ph, self.html, ph)

    def test_die_fristen_kommen_aus_den_konstanten(self):
        # Fest eingetragene Zahlen liessen sich getrennt vom Sender
        # verstellen, und der Wachhund liefe unbemerkt in die falsche
        # Richtung.
        self.assertIn(str(int(gs.TALLY_STALE_S * 1000)), self.html)
        self.assertIn(str(int(gs.TALLY_HEARTBEAT_S * 1000)), self.html)

    def test_offline_sieht_nicht_aus_wie_safe(self):
        safe = re.search(r"#bg\.safe\{background:(#[0-9a-fA-F]{3,6})\}", self.html)
        offline = re.search(r"#bg\.offline\{background:(#[0-9a-fA-F]{3,6})\}", self.html)
        self.assertIsNotNone(safe)
        self.assertIsNotNone(offline)
        self.assertNotEqual(safe.group(1).lower(), offline.group(1).lower())
        # Und nicht nur ungleich: `safe` ist fast schwarz, `offline` muss
        # HELL sein. Zwei dunkle Toene sind auf einem Handy im Tageslicht
        # dasselbe Bild.
        self.assertGreater(self._helligkeit(offline.group(1)),
                           self._helligkeit(safe.group(1)) + 60)

    @staticmethod
    def _helligkeit(hexfarbe):
        h = hexfarbe.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return 0.299 * r + 0.587 * g + 0.114 * b

    def test_kein_rueckfall_auf_safe(self):
        # Der beruhigende Zustand darf nie der Standard sein.
        self.assertNotIn("|| 'safe'", self.html)
        self.assertNotIn("||'safe'", self.html)
        self.assertIn("||'unknown'", self.html)

    def test_es_gibt_einen_wachhund(self):
        # Die drei Namen ALLEIN reichen nicht: eine Gegenprobe, die den Rumpf
        # des Intervalls durch `return;` ersetzte, blieb gruen -- alle drei
        # standen weiter da, und der Wachhund tat nichts mehr. Geprueft wird
        # deshalb der RUMPF: dass er `lastSeen` gegen `STALE_MS` haelt und
        # daraufhin auf `stale` schaltet.
        m = re.search(r"setInterval\(function\(\)\{(.*?)\},\s*\d+\)",
                      self.html, re.S)
        self.assertIsNotNone(m, "kein setInterval-Rumpf gefunden")
        rumpf = m.group(1)
        self.assertIn("lastSeen", rumpf)
        self.assertIn("STALE_MS", rumpf)
        self.assertIn("set('stale')", rumpf.replace('"', "'"))

    def test_ein_unbekannter_zustandsname_wird_zu_unknown(self):
        # Der Server kennt heute fuenf Namen. Kaeme je ein sechster dazu und
        # die Seite reichte ihn durch, stuende dort eine CSS-Klasse, die es
        # nicht gibt -- also der schwarze Grund aus `body`, und der sieht aus
        # wie `safe`. Genau der Fehler, gegen den diese ganze Datei steht.
        # Eine Gegenprobe, die diese Zeile entfernte, blieb zunaechst gruen.
        self.assertIn("if(!WORT.hasOwnProperty(state)) state='unknown';",
                      self.html.replace('"', "'"))
        for name in ("pgm", "pvw", "safe", "offline", "unknown", "stale"):
            self.assertIn(name + ":'", self.html.replace('"', "'"),
                          f"{name} fehlt in WORT")

    def test_stale_ist_ein_eigener_zustand_mit_wort(self):
        self.assertIn("stale:'KEINE VERBINDUNG'", self.html.replace('"', "'"))
        self.assertIn("#bg.stale{", self.html)

    def test_die_unsicheren_zustaende_stehen_auch_in_worten_da(self):
        # Farbe allein traegt die Auskunft nicht: ein Teil der Leute
        # unterscheidet Rot und Gruen nicht.
        for wort in ("KEINE VERBINDUNG", "MISCHER NICHT ERREICHBAR", "KEINE AUSKUNFT"):
            self.assertIn(wort, self.html)

    def test_pgm_und_safe_bekommen_KEIN_wort(self):
        # Die Farbe fuellt dort das ganze Bild und ist eindeutig; ein Wort
        # daueber waere Text, den im Ernstfall niemand liest.
        self.assertIn("pgm:''", self.html.replace('"', "'"))
        self.assertIn("safe:''", self.html.replace('"', "'"))

    def test_hoerbarer_alarm_beim_eintritt_in_pgm(self):
        # vdo.ninja#654 verlangt ihn ausdruecklich.
        self.assertIn("alarm()", self.html)
        self.assertIn("vorher!=='pgm'", self.html.replace('"', "'"))

    def test_der_ton_sagt_ob_er_wirklich_scharf_ist(self):
        # Ein stiller Alarm, den man fuer scharf haelt, ist schlimmer als
        # gar keiner.
        self.assertIn("Ton ist an", self.html)
        self.assertIn("Ton nicht moeglich", self.html)

    def test_der_name_wird_maskiert(self):
        # Er kommt bei einem Ad-hoc-Geraet aus dem langen Namen des
        # Mischers, also von einem Geraet im Netz.
        h = gs.render_tally_page("cam1", '<script>alert(1)</script>')
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;", h)

    def test_die_id_wird_maskiert(self):
        h = gs.render_tally_page('x"><b>', "n")
        self.assertNotIn('x"><b>', h)


if __name__ == "__main__":
    unittest.main()
