#!/usr/bin/env python3
"""
Was ein Tastendruck ausloest — EINE Rechnung fuer beide GPIO-Quellen.

─── WARUM DIESE DATEI ──────────────────────────────────────────────────────

Es gibt zwei Wege, an dem ein Taster haengen kann:

    Pi-Stecker   -> `gpio_watcher.py`   (libgpiod, 40-poliger Header)
    Numato-USB   -> `numato_watcher.py` (serielles Board, auch auf Mac/Windows)

Die LEITUNG ist verschieden, die ENTSCHEIDUNG ist es nicht: „diese Flanke,
also dieser ATEM-Befehl / dieser Companion-Druck" ist auf beiden Wegen
Wort fuer Wort dasselbe. Genau solche doppelten Entscheidungen sind im
uebrigen Repo als Defektform benannt („die zweite, die niemand nachzieht" —
`tally_state_for_device`, `tally_lampe_soll_leuchten`): eine faerbt eine
Lampe, die andere schaltet eine Kamera, und wenn sie auseinanderlaufen,
merkt es niemand vor der Sendung. Also steht sie hier EINMAL, und beide
Watcher rufen sie auf.

─── WARUM MIT EINGESCHOBENEN HELFERN ───────────────────────────────────────

`run_action` bekommt `log`, `log_event`, `atem_cmd`, `http_post` und die
Companion-Adresse als Argumente, statt sie zu importieren. Zwei Gruende: kein
Import-Ring (beide Watcher importieren dieses Modul, nicht umgekehrt), und die
Rechnung wird ohne echten Mischer und ohne Netz pruefbar — ein Test schiebt
Attrappen unter und sieht nach, WELCHER Befehl herauskaeme.

Die Uebersetzung `tally.json`-Geraet -> Bindung liegt ebenfalls hier
(`device_to_binding`), weil sie fuer beide Quellen bis auf EIN Feld gleich
ist: der Pi nennt die Leitung `bcm`, der Numato `channel`. Dieselbe Zahl aus
`in_gpio` steht am Pi fuer eine BCM-Nummer, am Numato fuer eine Kanalnummer.
"""
import urllib.parse


def device_to_binding(d, *, source):
    """Ein `tally.json`-Geraet mit `in_gpio` in eine Bindung uebersetzen.

    `source` ist "pi" oder "numato". Der einzige Unterschied ist der
    Schluessel der Leitung: "bcm" am Pi, "channel" am Numato — die Zahl aus
    `in_gpio` selbst ist dieselbe. Ohne `in_gpio` oder mit
    `in_action_type == "none"` gibt es keine Bindung (None).
    """
    line = d.get("in_gpio")
    if not isinstance(line, int):
        return None
    action_type = d.get("in_action_type", "none")
    if action_type == "none":
        return None
    if action_type in ("atem_aux", "atem_pgm", "atem_pvw"):
        src = d.get("in_atem_source")
        if not isinstance(src, int):
            src = d.get("input")  # Rueckfall: der eigene ATEM-Eingang des Geraets
        rel = d.get("in_atem_source_release")
        rel = rel if isinstance(rel, int) else None
        action = {"kind": action_type,
                  "source": src,
                  "source_release": rel,
                  "me": int(d.get("me") or 1)}
        if action_type == "atem_aux":
            action["aux"] = d.get("in_atem_aux")
    elif action_type == "companion":
        mode = d.get("in_companion_mode", "tap")
        kind = "down_up" if mode == "hold" else "press"
        action = {"kind": kind,
                  "page":   d.get("in_companion_page", 1),
                  "row":    d.get("in_companion_row", 0),
                  "column": d.get("in_companion_col", 0)}
    else:
        return None
    # Modi, die BEIDE Flanken brauchen (Druck UND Freigabe):
    #   - companion down_up
    #   - atem_* mit definierter Freigabe-Quelle
    #   - jede Bindung mit hold_release_ms > 0 (der Burst-Tracker braucht jede Flanke)
    edge = d.get("in_edge", "falling")
    kind = action.get("kind")
    is_atem = kind in ("atem_aux", "atem_pgm", "atem_pvw")
    hold_ms = int(d.get("in_hold_release_ms") or 0)
    needs_both = (kind == "down_up"
                  or (is_atem and action.get("source_release") is not None)
                  or hold_ms > 0)
    if needs_both:
        edge = "both"
    key = "bcm" if source == "pi" else "channel"
    return {
        key: line,
        "trigger_edge": edge,
        "enabled": True,
        "bias": d.get("in_bias", "pull-up"),
        "debounce_ms": int(d.get("in_debounce_ms", 20)),
        "hold_release_ms": hold_ms,
        "burst_min_edges": max(1, int(d.get("in_burst_min_edges") or 1)),
        "action": action,
        "_label": d.get("name") or d.get("id") or f"in_{line}",
    }


def run_action(binding, event_type, *, log, log_event, atem_cmd, http_post,
               companion, edge_count=None, log_fields=None):
    """Eine Flanke in einen Befehl umsetzen — ATEM (ueber `atem_cmd`) oder
    Companion (ueber `http_post`).

    Die Helfer kommen von aussen (siehe Modulkopf). `binding` traegt seinen
    eigenen `_label`, damit diese Funktion nicht wissen muss, ob die Leitung
    `bcm` oder `channel` heisst. `log_fields` haengt an jede Zeile des
    Eingabe-Ereignislogs dieselben Grundangaben (z. B. `{"bcm": 17}` am Pi,
    `{"channel": 17}` am Numato) — damit das Log sagt, WELCHE Leitung ausloeste.
    """
    trig = binding.get("trigger_edge", "falling")
    label = binding.get("_label") or "GPIO"
    grund = dict(log_fields or {})

    def _ilog(**extra):
        rec = dict(grund)
        rec.update({"edge": event_type, "label": label})
        if edge_count is not None:
            rec["edge_count"] = edge_count
        rec.update(extra)
        log_event("input", **rec)

    if trig != "both" and trig != event_type:
        # Flanke kam, aber die Bindung reagiert nicht darauf — protokollieren,
        # damit man sieht, dass der Pin ausgeloest hat.
        _ilog(action="ignored", reason=f"trigger={trig}")
        return
    a = binding.get("action") or {}
    kind = a.get("kind")
    try:
        if kind == "press":
            url = f"{companion}/api/location/{a['page']}/{a['row']}/{a['column']}/press"
            http_post(url)
            log(f"{label} {event_type} -> press {a['page']}/{a['row']}/{a['column']}")
            _ilog(action="companion_press",
                  page=a['page'], row=a['row'], column=a['column'], ok=True)
        elif kind == "down_up":
            sub = "down" if event_type == "falling" else "up"
            url = f"{companion}/api/location/{a['page']}/{a['row']}/{a['column']}/{sub}"
            http_post(url)
            log(f"{label} {event_type} -> {sub} {a['page']}/{a['row']}/{a['column']}")
            _ilog(action="companion_" + sub,
                  page=a['page'], row=a['row'], column=a['column'], ok=True)
        elif kind == "variable":
            name = urllib.parse.quote(a["variable"])
            val = str(a.get("value", "1"))
            url = f"{companion}/api/custom-variable/{name}/value"
            http_post(url, val.encode())
            log(f"{label} {event_type} -> variable {a['variable']}={val}")
            _ilog(action="companion_variable",
                  variable=a['variable'], value=val, ok=True)
        elif kind in ("atem_aux", "atem_pgm", "atem_pvw"):
            src_press   = a.get("source")
            src_release = a.get("source_release")
            configured = binding.get("trigger_edge", "falling")
            if configured == "rising":
                is_press = (event_type == "rising")
            else:
                is_press = (event_type == "falling")
            target = src_press if is_press else src_release
            phase = "press" if is_press else "release"
            if target is None:
                if not is_press:
                    return
                target = src_press
            if not isinstance(target, int):
                log(f"{label}: {kind} missing source ({a})")
                _ilog(action=kind, phase=phase, ok=False, error="missing source")
                return
            if kind == "atem_aux":
                aux = int(a.get("aux") or 0)
                if not aux:
                    _ilog(action=kind, phase=phase, ok=False,
                          error="missing aux number")
                    return
                atem_cmd({"cmd": "set_aux", "aux": aux, "source": target})
                log(f"{label} {event_type}({phase}) -> ATEM Aux{aux} <- src {target}")
                _ilog(action=kind, phase=phase, aux=aux, source=target, ok=True)
            else:
                me = int(a.get("me") or 1)
                sub = "set_program" if kind == "atem_pgm" else "set_preview"
                atem_cmd({"cmd": sub, "me": me, "source": target})
                bus = "PGM" if kind == "atem_pgm" else "PVW"
                log(f"{label} {event_type}({phase}) -> ATEM {bus} ME{me} <- src {target}")
                _ilog(action=kind, phase=phase, me=me, source=target, ok=True)
        else:
            log(f"{label}: unknown action kind {kind!r}")
            _ilog(action="unknown", kind=str(kind), ok=False)
    except Exception as e:
        log(f"action error on {label}: {e}")
        _ilog(action=str(kind), ok=False, error=str(e))
