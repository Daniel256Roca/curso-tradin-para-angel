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

# =============================== INFORME ====================================
def barra(v, escala=4):
    n = max(0, min(20, int(v / escala)))
    return "█" * n

def informe(nombre, activo, velas, m, problemas, avisos):
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
    print("  📌 Recuerda: esto es pasado. Antes de poner un bot en real: forward-test")
    print("     en demo 2-3 meses y riesgo pequeño. El backtest es el filtro, no el diploma.")
    print(L); print()

# =============================== MAIN =======================================
EJEMPLO = '''# Guarda esto como mi_estrategia.py y pruébalo:
STOP_ATR  = 2.0    # stop = 2 × ATR(14)
TAKE_ATR  = 3.0    # objetivo = 3 × ATR(14)
RIESGO_PCT = 1.0

def senal(i, velas):
    if i < 30: return 0
    cierres = [v["c"] for v in velas]
    e9a, e21a = ema(cierres[:-1], 9),  ema(cierres[:-1], 21)
    e9b, e21b = ema(cierres, 9),       ema(cierres, 21)
    if e9a <= e21a and e9b > e21b: return 1    # cruce al alza  → comprar
    if e9a >= e21a and e9b < e21b: return -1   # cruce a la baja → vender
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
    informe(args.estrategia, nombre_activo, velas, m, problemas, avisos)

if __name__ == "__main__":
    main()
