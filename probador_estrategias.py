#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PROBADOR DE ESTRATEGIAS ALGORÍTMICAS 🧪
=======================================
Sube tu estrategia en Python, carga datos REALES (oro, DAX, Nasdaq, SP500,
EURUSD, GBPUSD desde stooq) y te dice:
  · si es rentable de verdad (profit factor, drawdown, expectativa…)
  · qué PROBLEMAS tiene (poca muestra, sin stop, suerte concentrada…)

SIN DEPENDENCIAS: solo Python 3 estándar. Nada de pip install.

USO:
    python3 probador_estrategias.py mi_estrategia.py --activo oro
    python3 probador_estrategias.py mi_estrategia.py --activo dax --dias 2000
    python3 probador_estrategias.py mi_estrategia.py --csv mis_datos.csv

TU ESTRATEGIA: un archivo .py con estas piezas (todas opcionales menos `senal`):

    STOP_PIPS  = 30        # stop fijo en puntos  (o si prefieres:)
    STOP_ATR   = 2.0       # stop = 2 × ATR(14)
    TAKE_PIPS  = 60        # objetivo fijo        (o:)
    TAKE_ATR   = 3.0       # objetivo = 3 × ATR(14)
    RIESGO_PCT = 1.0       # % de la cuenta arriesgado por operación (def. 1)

    def senal(i, velas):
        # velas[i] = {"fecha": "2024-01-15", "o":…, "h":…, "l":…, "c":…}
        # Solo puedes mirar velas[0..i] (el pasado). Devuelve:
        #    1  → quiero estar COMPRADO
        #   -1  → quiero estar VENDIDO
        #    0  → nada / fuera
        ...

La entrada se ejecuta en la APERTURA de la vela siguiente (sin trampas de
futuro). Tienes gratis las funciones ema(), sma(), rsi(), atr(), maximo(),
minimo() — úsalas directamente dentro de tu archivo.

CONTROL ANTI-SOBREAJUSTE 🛡️ (automático):
  · Divide el histórico: 70% "entrenamiento" + 30% final "prueba ciega". Si
    solo funciona en el primero → sobreajuste clásico, y te lo dice.
  · Toda variable TUYA en MAYÚSCULAS que sea un número (p. ej. PERIODO = 14)
    se considera parámetro ajustable: el probador lo mueve ±25% y mide si tu
    estrategia se rompe (parámetro FRÁGIL = no lo afines más) o aguanta
    (ESTABLE = el mercado no depende de tu afinación fina).
  · Regla de oro que te va a repetir: usa pocos parámetros, valores redondos
    y canónicos (10/20/50, RSI 14…), y exige que funcione en toda la vecindad,
    no solo en el punto exacto que probaste.
"""
import argparse, csv, io, math, statistics, sys, urllib.request

# =============================== INDICADORES (gratis para tu estrategia) ====
def ema(valores, periodo):
    """EMA del último tramo de `valores` (lista de floats). Devuelve un float."""
    if len(valores) < periodo:
        return valores[-1] if valores else 0.0
    k = 2 / (periodo + 1)
    e = sum(valores[:periodo]) / periodo
    for v in valores[periodo:]:
        e = v * k + e * (1 - k)
    return e

def sma(valores, periodo):
    if len(valores) < periodo or periodo <= 0:
        return valores[-1] if valores else 0.0
    return sum(valores[-periodo:]) / periodo

def rsi(valores, periodo=14):
    if len(valores) <= periodo:
        return 50.0
    gan, per = [], []
    for a, b in zip(valores[-periodo-1:-1], valores[-periodo:]):
        d = b - a
        gan.append(max(d, 0)); per.append(max(-d, 0))
    ag = sum(gan) / periodo; ap = sum(per) / periodo
    if ap == 0:
        return 100.0
    return 100 - 100 / (1 + ag / ap)

def atr(velas, periodo=14):
    if len(velas) <= periodo:
        periodo = max(1, len(velas) - 1)
    trs = []
    tramo = velas[-periodo-1:]
    for j in range(1, len(tramo)):
        h, l, pc = tramo[j]["h"], tramo[j]["l"], tramo[j-1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 1e-9

def maximo(velas, periodo, campo="h"):
    return max(v[campo] for v in velas[-periodo:])

def minimo(velas, periodo, campo="l"):
    return min(v[campo] for v in velas[-periodo:])

# =============================== DATOS ======================================
ACTIVOS = {
    # clave: (símbolo stooq, símbolo Yahoo, spread típico en puntos)
    "oro":    ("^xauusd", "GC=F",     0.5),
    "xauusd": ("^xauusd", "GC=F",     0.5),
    "plata":  ("^xagusd", "SI=F",     0.03),
    "dax":    ("^dax",    "^GDAXI",   2.0),
    "nasdaq": ("^ndx",    "^NDX",     3.0),
    "sp500":  ("^spx",    "^GSPC",    1.0),
    "eurusd": ("eurusd",  "EURUSD=X", 0.0002),
    "gbpusd": ("gbpusd",  "GBPUSD=X", 0.0003),
}

def descargar_stooq(simbolo):
    url = "https://stooq.com/q/d/l/?s=%s&i=d" % urllib.request.quote(simbolo)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode("utf-8", "replace")
    velas = []
    for fila in csv.DictReader(io.StringIO(txt)):
        try:
            c = float(fila["Close"])
            if c <= 0: continue
            velas.append({
                "fecha": fila["Date"],
                "o": float(fila["Open"]), "h": float(fila["High"]),
                "l": float(fila["Low"]),  "c": c,
            })
        except (ValueError, KeyError, TypeError):
            continue
    return velas

def descargar_yahoo(simbolo):
    import json, datetime
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=10y&interval=1d"
           % urllib.request.quote(simbolo))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        datos = json.loads(r.read().decode("utf-8", "replace"))
    res = datos["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    velas = []
    for k, t in enumerate(ts):
        try:
            c = q["close"][k]
            if c is None or c <= 0: continue
            velas.append({
                "fecha": datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                "o": q["open"][k] or c, "h": q["high"][k] or c,
                "l": q["low"][k] or c, "c": c,
            })
        except (IndexError, TypeError):
            continue
    return velas

def descargar_activo(clave):
    sim_stooq, sim_yahoo, spread = ACTIVOS[clave]
    try:
        velas = descargar_stooq(sim_stooq)
        if len(velas) >= 120:
            return velas, f"{clave} (stooq {sim_stooq}, diario)", spread
    except Exception:
        pass
    print("   (stooq no responde; intento con Yahoo Finance…)")
    velas = descargar_yahoo(sim_yahoo)
    return velas, f"{clave} (Yahoo {sim_yahoo}, diario)", spread

def cargar_csv(ruta):
    velas = []
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        lector = csv.DictReader(f)
        cols = { (c or "").strip().lower(): c for c in (lector.fieldnames or []) }
        def col(*nombres):
            for n in nombres:
                if n in cols: return cols[n]
            return None
        cf, co, ch, cl, cc = col("fecha","date","time"), col("open","o","apertura"), \
                             col("high","h","max","alto"), col("low","l","min","bajo"), \
                             col("close","c","cierre","last")
        if not cc:
            sys.exit("❌ Tu CSV necesita al menos una columna 'close' (y idealmente open/high/low/date).")
        for fila in lector:
            try:
                c = float((fila[cc] or "").replace(",", "."))
                o = float((fila[co] or c).replace(",", ".")) if co else c
                h = float((fila[ch] or c).replace(",", ".")) if ch else c
                l = float((fila[cl] or c).replace(",", ".")) if cl else c
                velas.append({"fecha": (fila[cf] if cf else "") or "", "o": o, "h": h, "l": l, "c": c})
            except ValueError:
                continue
    return velas

# =============================== MOTOR BACKTEST =============================
def ejecutar_backtest(velas, senal, cfg, spread):
    n = len(velas)
    warm = min(60, n // 5)
    riesgo = cfg.get("RIESGO_PCT", 1.0) / 100.0
    stop_p, take_p = cfg.get("STOP_PIPS"), cfg.get("TAKE_PIPS")
    stop_a, take_a = cfg.get("STOP_ATR"), cfg.get("TAKE_ATR")

    trades, senal_prev, pos = [], 0, None
    for i in range(warm, n):
        s = 0
        try:
            s = senal(i, velas[:i+1])
        except Exception:
            s = 0
        s = 1 if s and s > 0 else (-1 if s and s < 0 else 0)

        if pos is not None:
            v = velas[i]
            salida = None; motivo = ""
            # gestión intrabar conservadora: primero el STOP (lo malo primero)
            if pos["dir"] == 1:
                if pos["sl"] is not None and v["l"] <= pos["sl"]:
                    salida, motivo = pos["sl"], "SL"
                elif pos["tp"] is not None and v["h"] >= pos["tp"]:
                    salida, motivo = pos["tp"], "TP"
            else:
                if pos["sl"] is not None and v["h"] >= pos["sl"]:
                    salida, motivo = pos["sl"], "SL"
                elif pos["tp"] is not None and v["l"] <= pos["tp"]:
                    salida, motivo = pos["tp"], "TP"
            if salida is None and s != 0 and s != pos["dir"]:
                if i + 1 < n:
                    salida, motivo = velas[i+1]["o"] + (spread/2 if pos["dir"] == -1 else -spread/2), "SEÑAL"
            if salida is None and i == n - 1:
                salida, motivo = v["c"], "FIN"
            if salida is not None:
                pts = (salida - pos["entrada"]) * pos["dir"]
                trades.append({"dir": pos["dir"], "entrada": pos["entrada"], "salida": salida,
                               "pts": pts, "sl_dist": pos["sl_dist"], "motivo": motivo,
                               "fecha_e": pos["fecha"], "fecha_s": v["fecha"], "barras": i - pos["i"]})
                pos = None
                if s != 0 and salida is not None and motivo != "FIN" and i + 1 < n and s != senal_prev:
                    pass  # la nueva entrada se evalúa abajo con la lógica normal
        if pos is None and s != 0 and s != senal_prev and i + 1 < n:
            a = atr(velas[:i+1]) or 1e-9
            sl_dist = stop_p if stop_p else (stop_a * a if stop_a else None)
            tp_dist = take_p if take_p else (take_a * a if take_a else None)
            ent = velas[i+1]["o"]
            if s == 1:
                ent += spread / 2
                sl = ent - sl_dist if sl_dist else None
                tp = ent + tp_dist if tp_dist else None
            else:
                ent -= spread / 2
                sl = ent + sl_dist if sl_dist else None
                tp = ent - tp_dist if tp_dist else None
            if sl_dist is None:
                sl_dist = 2 * a  # stop virtual SOLO para dimensionar el riesgo
            pos = {"dir": s, "entrada": ent, "sl": sl, "tp": tp,
                   "sl_dist": sl_dist, "fecha": velas[i+1]["fecha"], "i": i + 1}
        senal_prev = s

    # equity simulada con riesgo % compuesto
    equity, curva = 100.0, [100.0]
    for t in trades:
        r = (t["pts"] / t["sl_dist"]) if t["sl_dist"] > 0 else 0.0
        t["pnl_pct"] = riesgo * r * 100
        equity *= (1 + riesgo * r)
        curva.append(equity)
    return trades, curva

# =============================== MÉTRICAS ===================================
def metricas(trades, curva, velas):
    wins  = [t for t in trades if t["pts"] > 0]
    loses = [t for t in trades if t["pts"] <= 0]
    gp = sum(t["pts"] for t in wins)
    gl = abs(sum(t["pts"] for t in loses)) or 1e-12
    pf = gp / gl if loses else (float("inf") if gp > 0 else 0)
    wr = len(wins) / len(trades) * 100 if trades else 0
    pnl_tot = sum(t["pts"] for t in trades)

    pico, mdd, dur_dd = curva[0], 0.0, 0
    desde_pico, max_dur = 0, 0
    for e in curva:
        if e >= pico: pico, desde_pico = e, 0
        else:
            desde_pico += 1; max_dur = max(max_dur, desde_pico)
            mdd = max(mdd, (pico - e) / pico * 100)

    rets = [math.log(curva[i] / curva[i-1]) for i in range(1, len(curva)) if curva[i-1] > 0]
    ops_anyo_calc = len(trades) / max(len(velas) / 252, 1e-9)
    sharpe = (statistics.mean(rets) / statistics.pstdev(rets) * math.sqrt(ops_anyo_calc)) \
             if len(rets) > 2 and statistics.pstdev(rets) > 0 else 0.0

    racha, max_racha_neg = 0, 0
    for t in trades:
        if t["pts"] <= 0: racha += 1; max_racha_neg = max(max_racha_neg, racha)
        else: racha = 0

    orden = sorted(trades, key=lambda t: t["pnl_pct"], reverse=True)
    conc = (sum(t["pnl_pct"] for t in orden[:3]) / (sum(t["pnl_pct"] for t in wins) or 1e-12)) * 100 if wins else 0
    dias = max(1, (len(velas)))
    anyos = dias / 252
    return {
        "trades": len(trades), "wins": len(wins), "loses": len(loses), "wr": wr,
        "pf": pf, "pnl_pts": pnl_tot, "ret_pct": curva[-1] - 100,
        "mdd": mdd, "sharpe": sharpe, "max_racha_neg": max_racha_neg,
        "expect_pts": pnl_tot / len(trades) if trades else 0,
        "expect_pct": (curva[-1] / 100) ** (1 / max(1, len(trades))) - 1 if trades else 0,
        "conc3": conc, "ops_anyo": len(trades) / anyos if anyos > 0 else 0,
        "sl_count": sum(1 for t in trades if t["motivo"] == "SL"),
        "tp_count": sum(1 for t in trades if t["motivo"] == "TP"),
        "sena_count": sum(1 for t in trades if t["motivo"] == "SEÑAL"),
        "barras_media": statistics.mean([t["barras"] for t in trades]) if trades else 0,
        "max_dur_dd": max_dur, "equidad_final": curva[-1],
        "buyhold_pct": (velas[-1]["c"] / velas[0]["o"] - 1) * 100,
    }

def diagnosticar(m, cfg, trades):
    problemas, avisos = [], []
    if m["trades"] < 30:
        problemas.append(f"Muestra insuficiente: solo {m['trades']} operaciones. Con menos de ~30 no hay "
                         f"estadística seria; amplía el histórico o baja el marco temporal.")
    if cfg.get("STOP_PIPS") is None and cfg.get("STOP_ATR") is None:
        problemas.append("Tu estrategia NO define STOP (ni STOP_PIPS ni STOP_ATR). El probador ha usado uno "
                         "virtual de 2×ATR solo para simular, pero operar sin stop real es la forma nº1 de volar una cuenta.")
    if m["pf"] < 1:
        problemas.append(f"Profit factor {m['pf']:.2f} < 1: pierdes más de lo que ganas. Tal cual, NO es operable.")
    elif m["pf"] < 1.3:
        avisos.append(f"Profit factor {m['pf']:.2f}: marginal. Con spread, deslizamiento y malas rachas reales puede quedarse en nada.")
    if m["mdd"] > 25:
        problemas.append(f"Drawdown máximo del {m['mdd']:.0f}%: la mayoría de humanos abandonan (o hacen locuras) "
                         f"mucho antes de recuperarse de eso.")
    elif m["mdd"] > 15:
        avisos.append(f"Drawdown del {m['mdd']:.0f}%: considerable. ¿Lo aguantarías en vivo sin apagar el robot?")
    if m["conc3"] > 40 and m["wins"] > 0:
        problemas.append(f"El {m['conc3']:.0f}% de tus ganancias viene de solo 3 operaciones: dependes de la suerte "
                         f"de un par de días. Sin esas 3, el sistema cambia por completo.")
    if m["expect_pts"] <= 0:
        problemas.append("Expectativa por operación ≤ 0: cada clic tuyo destruye valor de media.")
    if m["max_racha_neg"] >= 8:
        problemas.append(f"Racha máxima de {m['max_racha_neg']} pérdidas seguidas. Con riesgo del "
                         f"{cfg.get('RIESGO_PCT', 1.0)}% eso son ~{m['max_racha_neg']*cfg.get('RIESGO_PCT', 1.0):.0f}% de cuenta en una mala racha.")
    if m["ops_anyo"] < 4:
        avisos.append(f"Solo {m['ops_anyo']:.1f} operaciones/año: tardarás una eternidad en saber si funciona en vivo.")
    if m["wins"] > 0 and m["loses"] > 0:
        gw = sum(t["pts"] for t in trades if t["pts"] > 0) / m["wins"]
        gl = abs(sum(t["pts"] for t in trades if t["pts"] <= 0)) / m["loses"]
        if gw < gl * 0.8 and m["wr"] < 50:
            avisos.append(f"Ganas poco ({gw:.1f} pts) y pierdes mucho ({gl:.1f} pts) con un winrate del {m['wr']:.0f}%: "
                          f"una pequeña variación del mercado rompe el equilibrio.")
    if m["sl_count"] == 0 and (cfg.get("STOP_PIPS") or cfg.get("STOP_ATR")):
        avisos.append("Nunca saltó el stop: o es demasiado ancho, o tu muestra es demasiado amable.")
    return problemas, avisos

# =================== ANTI-SOBREAJUSTE / ANTI-SOBREOPTIMIZACIÓN ==============
EXCLUIDOS_SENSIBILIDAD = {"RIESGO_PCT"}   # el tamaño de posición NO es un parámetro del alpha

def _fresh_ns():
    return {"ema": ema, "sma": sma, "rsi": rsi, "atr": atr, "maximo": maximo, "minimo": minimo}

def _cfg_de(ns):
    return {k: ns.get(k) for k in ("STOP_PIPS","TAKE_PIPS","STOP_ATR","TAKE_ATR","RIESGO_PCT") if ns.get(k) is not None}

def detectar_parametros(ns):
    """Toda variable EN MAYÚSCULAS de tipo int/float = parámetro ajustable."""
    params = {}
    for k, v in ns.items():
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and k == k.upper() and any(ch.isalpha() for ch in k)
                and not k.startswith("_") and k not in EXCLUIDOS_SENSIBILIDAD):
            params[k] = v
    return params

def prueba_ciega(velas, codigo, spread):
    """70% entrenamiento vs 30% final que la estrategia 'nunca vio'."""
    corte = int(len(velas) * 0.7)
    def run(tramo):
        ns2 = _fresh_ns()
        exec(compile(codigo, "<estrategia>", "exec"), ns2)
        t, c = ejecutar_backtest(tramo, ns2.get("senal"), _cfg_de(ns2), spread)
        return metricas(t, c, tramo) if len(t) >= 3 else None
    return run(velas[:corte]), run(velas[max(0, corte - 60):])  # 60 velas de calentamiento

def sensibilidad(velas, codigo, params, spread):
    """Mueve cada parámetro ±25%: si solo funciona en tu punto exacto → sobreoptimización."""
    tabla = []
    for nombre, valor in params.items():
        ensayos = []
        for f in (0.75, 1.25):
            v = valor * f
            if isinstance(valor, int):
                v = max(1, int(round(v)))
            if v == valor:  # int pequeños (p. ej. 1): fuerza que cambie de verdad
                v = valor - 1 if f < 1 else valor + 1
            ns2 = _fresh_ns()
            try:
                exec(compile(codigo, "<estrategia>", "exec"), ns2)
                ns2[nombre] = v   # se pisa DESPUÉS del exec: senal lo leerá al ejecutarse
                t2, c2 = ejecutar_backtest(velas, ns2.get("senal"), _cfg_de(ns2), spread)
                m2 = metricas(t2, c2, velas) if len(t2) >= 3 else None
                ensayos.append((f, v, m2["pf"] if m2 else None, len(t2)))
            except Exception:
                ensayos.append((f, v, None, -1))
        tabla.append((nombre, valor, ensayos))
    return tabla

def clasificar_param(pf_base, ensayos):
    pfs = [e[2] for e in ensayos if e[2] is not None]
    if len(pfs) < 2:
        return "sin datos", pfs
    lo, hi = min(pfs), max(pfs)
    if pf_base >= 1 and lo < 1:
        return "fragil", pfs
    if pf_base > 0 and (hi - lo) > 0.5 * max(abs(pf_base), 0.5):
        return "sensible", pfs
    return "estable", pfs

# =============================== INFORME ====================================
def barra(v, escala=4):
    n = max(0, min(20, int(v / escala)))
    return "█" * n

def informe(nombre, activo, velas, m, problemas, avisos, blind=None, sens=None):
    L = "═" * 66
    print(); print(L)
    print(f"  🧪 INFORME DEL PROBADOR — {nombre}")
    print(f"  Activo: {activo}  ·  {len(velas)} velas diarias  ·  {velas[0]['fecha']} → {velas[-1]['fecha']}")
    print(L)
    print(f"  Operaciones totales.... {m['trades']}   ({m['ops_anyo']:.1f}/año, duración media {m['barras_media']:.0f} velas)")
    print(f"  Ganadas / perdidas..... {m['wins']} / {m['loses']}   (winrate {m['wr']:.1f}%)")
    print(f"  Profit factor.......... {m['pf']:.2f}" + ("  ✅" if m['pf'] >= 1.3 else ("  ⚠️" if m['pf'] >= 1 else "  ❌")))
    print(f"  Resultado en puntos.... {m['pnl_pts']:+.1f}")
    print(f"  Rentabilidad simulada.. {m['ret_pct']:+.1f}%   (comprar y mantener: {m['buyhold_pct']:+.1f}%)")
    print(f"  Expectativa/trade...... {m['expect_pts']:+.2f} pts")
    print(f"  Drawdown máximo........ {m['mdd']:.1f}%   (peor racha: {m['max_racha_neg']} "
          f"{'pérdida seguida' if m['max_racha_neg'] == 1 else 'pérdidas seguidas'})")
    print(f"  Sharpe (anualizado).... {m['sharpe']:.2f}")
    print(f"  Cierres: SL {m['sl_count']} · TP {m['tp_count']} · señal contraria {m['sena_count']}")
    print()
    # veredicto
    if problemas and m["pf"] < 1:
        veredicto, emoji = "NO RENTABLE", "❌"
    elif problemas:
        veredicto, emoji = "RENTABLE EN PAPEL, PERO CON PEGAS SERIAS", "⚠️"
    elif avisos and (m["pf"] < 1.3 or m["trades"] < 40):
        veredicto, emoji = "PROMETE, PERO AÚN NO ES CONFIABLE", "🟡"
    else:
        veredicto, emoji = "RENTABLE Y ESTADÍSTICAMENTE DIGNA", "✅"
    print(f"  {emoji} VEREDICTO: {veredicto}")
    print()
    if problemas:
        print("  🚨 PROBLEMAS DETECTADOS:")
        for i, p in enumerate(problemas, 1):
            print(f"   {i}. {p}")
        print()
    if avisos:
        print("  ⚠️  AVISOS (no mortales, pero apúntalos):")
        for i, a in enumerate(avisos, 1):
            print(f"   {i}. {a}")
        print()
    # ---------------- anti-sobreajuste ----------------
    if blind is not None or sens is not None:
        print("  🛡️ CONTROL ANTI-SOBREAJUSTE:")
        if blind is not None:
            is_m, os_m = blind
            if is_m:
                print(f"   Entrenamiento (70%):.. PF {is_m['pf']:.2f} · {is_m['trades']} ops · {is_m['ret_pct']:+.1f}%")
            if os_m:
                marca = "🚨" if (is_m and is_m["pf"] >= 1.2 and os_m["pf"] < 1) else ("⚠️" if is_m and os_m["pf"] < is_m["pf"] * 0.7 else "✅")
                print(f"   Prueba ciega (30%):.. PF {os_m['pf']:.2f} · {os_m['trades']} ops · {os_m['ret_pct']:+.1f}%   {marca}")
            elif is_m:
                print("   Prueba ciega (30%):.. apenas generó operaciones; amplía el histórico para fiarte del filtro.")
        if sens:
            print("   Sensibilidad de parámetros (±25%):")
            for nombre, valor, ensayos in sens:
                estado, pfs = clasificar_param(m["pf"], ensayos)
                if estado == "sin datos":
                    print(f"     {nombre} = {valor:<10g} movido ±25% → no genera operaciones suficientes "
                          f"({min(e[3] for e in ensayos)} ops)  ⚠️ parámetro TENso: se rompe al tocarlo")
                else:
                    lo, hi = min(pfs), max(pfs)
                    sym = {"fragil": "🚨 FRÁGIL", "sensible": "⚠️ sensible", "estable": "✅ estable"}[estado]
                    vals = " ~ ".join(f"{p:.2f}" for p in (lo, hi))
                    print(f"     {nombre} = {valor:<10g} PF oscila {vals}  {sym}")
        print()
        print("  💊 QUÉ AJUSTAR (receta anti-sobreoptimización):")
        receta = []
        if blind is not None:
            is_m, os_m = blind
            if is_m and os_m and is_m["pf"] >= 1.2 and os_m["pf"] < 1:
                receta.append("Tu sistema funciona 'de memoria': SIMPLIFICA. Quita filtros/reglas (cada regla es una "
                              "lotería jugada contra el pasado) y no la reoptimices hasta que quede bonita de nuevo: "
                              "eso ES sobreoptimizar.")
            elif is_m and os_m and is_m["pf"] > 1 and os_m["pf"] < is_m["pf"] * 0.7:
                receta.append("Degrada fuera de muestra: baja la precisión de tus parámetros (valores más redondos, "
                              "menos decimales mágicos).")
        if sens:
            for nombre, valor, ensayos in sens:
                estado, _p = clasificar_param(m["pf"], ensayos)
                if estado == "fragil":
                    receta.append(f"{nombre}: NO lo afines más. Pon un valor redondo y común (p. ej. medias 10/20/50, "
                                  f"RSI 14, ATR 2-3×) y asume como 'real' el PEOR resultado de su vecindad, no el tuyo.")
                elif estado == "sensible":
                    receta.append(f"{nombre}: puedes moverlo, pero ajuste fino = sobreoptimización. Si lo tocas, "
                                  f"exige que medio entorno (±25%) siga ganando.")
                elif estado == "sin datos":
                    receta.append(f"{nombre}: con ±25% tu estrategia casi deja de operar → está al filo; dale holgura "
                                  f"(condiciones menos estrictas).")
        if sens and any(clasificar_param(m["pf"], e)[0] == "estable" for _, _, e in sens):
            nombres_ok = [n for n, _, e in sens if clasificar_param(m["pf"], e)[0] == "estable"]
            receta.append(f"Estables ({', '.join(nombres_ok)}): aquí NO hay nada que tocar — tu ventaja no depende "
                          f"del valor exacto. Tocarlos más es pintar sobre el lienzo.")
        if sens is not None and len(sens) > 4:
            receta.append(f"Tienes {len(sens)} parámetros: cada uno es otra puerta al sobreajuste. Parsimonia: "
                          f"máximo 3-4 piezas móviles por estrategia.")
        receta.append("La meta NO es el backtest más alto: es la MESETA donde todo el entorno (±25%, otra época, "
                      "otro activo similar) sigue funcionando. El máximo del backtest siempre es el punto más sobreajustado.")
        for i, r in enumerate(receta, 1):
            print(f"   {i}. {r}")
        print()
    print("  📌 Recuerda: esto es pasado. Antes de poner un bot en real: forward-test")
    print("     en demo 2-3 meses y riesgo pequeño. El backtest es el filtro, no el diploma.")
    print(L); print()

# =============================== MAIN =======================================
EJEMPLO = '''# Guarda esto como mi_estrategia.py y pruébalo:
PERIODO_RAPIDA = 9     # ← parámetros en MAYÚSCULAS: el probador los moverá ±25%
PERIODO_LENTA  = 21
STOP_ATR  = 2.0    # stop = 2 × ATR(14)
TAKE_ATR  = 3.0    # objetivo = 3 × ATR(14)
RIESGO_PCT = 1.0

def senal(i, velas):
    if i < PERIODO_LENTA + 5: return 0
    cierres = [v["c"] for v in velas]
    ra, la = ema(cierres[:-1], PERIODO_RAPIDA), ema(cierres[:-1], PERIODO_LENTA)
    rb, lb = ema(cierres, PERIODO_RAPIDA),      ema(cierres, PERIODO_LENTA)
    if ra <= la and rb > lb: return 1    # cruce al alza  → comprar
    if ra >= la and rb < lb: return -1   # cruce a la baja → vender
    return 0
'''

def main():
    ap = argparse.ArgumentParser(description="Probador de estrategias algorítmicas (datos reales, informe en español).")
    ap.add_argument("estrategia", help="tu archivo .py con la función senal(i, velas)")
    ap.add_argument("--activo", default="oro", help="oro, plata, dax, nasdaq, sp500, eurusd, gbpusd (def. oro)")
    ap.add_argument("--csv", help="usa tu propio CSV (date,open,high,low,close) en vez de descargar")
    ap.add_argument("--dias", type=int, default=1500, help="máx. velas diarias a usar (def. 1500 ≈ 6 años)")
    ap.add_argument("--ejemplo", action="store_true", help="imprime una estrategia de ejemplo y sale")
    args = ap.parse_args()

    if args.ejemplo:
        print(EJEMPLO); return

    # 1) datos
    if args.csv:
        velas = cargar_csv(args.csv); nombre_activo = args.csv; spread = 0.0
    else:
        clave = args.activo.lower()
        if clave not in ACTIVOS:
            sys.exit("❌ Activo desconocido. Opciones: " + ", ".join(sorted(set(ACTIVOS))))
        print(f"⬇️  Descargando {clave}…")
        try:
            velas, nombre_activo, spread = descargar_activo(clave)
        except Exception as e:
            sys.exit(f"❌ No pude descargar datos ({type(e).__name__}). Revisa tu conexión "
                     f"o usa --csv con un archivo tuyo (date,open,high,low,close).")
    if len(velas) < 120:
        sys.exit(f"❌ Solo {len(velas)} velas: insuficientes. Necesito al menos ~120 (mejor 500+).")
    velas = velas[-args.dias:]

    # 2) cargar estrategia (con indicadores inyectados)
    try:
        codigo = open(args.estrategia, encoding="utf-8").read()
    except OSError:
        sys.exit(f"❌ No encuentro '{args.estrategia}'.")
    ns = {"ema": ema, "sma": sma, "rsi": rsi, "atr": atr, "maximo": maximo, "minimo": minimo}
    try:
        exec(compile(codigo, args.estrategia, "exec"), ns)
    except Exception as e:
        sys.exit(f"❌ Tu estrategia tiene un error de Python: {type(e).__name__}: {e}")
    senal = ns.get("senal")
    if not callable(senal):
        sys.exit("❌ Tu archivo debe definir:  def senal(i, velas): …  (1=comprar, -1=vender, 0=nada)\n"
                 "   Ejecuta con --ejemplo para ver una plantilla completa.")
    cfg = {k: ns.get(k) for k in ("STOP_PIPS", "TAKE_PIPS", "STOP_ATR", "TAKE_ATR", "RIESGO_PCT") if ns.get(k) is not None}

    # 3) probar
    trades, curva = ejecutar_backtest(velas, senal, cfg, spread)
    if not trades:
        sys.exit("🤷 Tu estrategia no generó NI UNA operación en el histórico. "
                 "Revisa la lógica (¿condiciones imposibles? ¿índices fuera de rango?).")
    m = metricas(trades, curva, velas)
    problemas, avisos = diagnosticar(m, cfg, trades)

    # 4) control anti-sobreajuste
    blind, sens = None, None
    if len(velas) >= 200:
        print("🛡️  Control anti-sobreajuste: prueba ciega 70/30 + sensibilidad ±25%…")
        params = detectar_parametros(ns)
        blind = prueba_ciega(velas, codigo, spread)
        if params:
            sens = sensibilidad(velas, codigo, params, spread)
        elif len(velas) >= 200:
            avisos.append("No detecto parámetros tuyos en MAYÚSCULAS: bien por parsimonia… a menos que la lógica "
                          "esté 'cableada' a este periodo concreto (eso también es sobreajuste, solo que disfrazado).")
        is_m, os_m = blind
        if is_m and os_m:
            if is_m["pf"] >= 1.2 and os_m["pf"] < 1:
                problemas.append(f"Sobreajuste clásico: entrenamiento PF {is_m['pf']:.2f} pero prueba ciega PF "
                                 f"{os_m['pf']:.2f}. Se sabe el pasado de memoria.")
            elif is_m["pf"] > 1 and os_m["pf"] < is_m["pf"] * 0.6:
                avisos.append(f"Se degrada mucho fuera de muestra (PF {is_m['pf']:.2f} → {os_m['pf']:.2f}): "
                              f"parcialmente sobreajustada.")
    else:
        avisos.append("Histórico < 200 velas: no hay datos suficientes para la prueba ciega 70/30. "
                      "Amplía el histórico para fiarte del filtro anti-sobreajuste.")

    informe(args.estrategia, nombre_activo, velas, m, problemas, avisos, blind, sens)

if __name__ == "__main__":
    main()
