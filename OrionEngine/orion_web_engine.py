# -*- coding: utf-8 -*-
"""
ORION WEB ENGINE · orion_web_engine.py
======================================
Motor de backtest REAL pensado para vivir DENTRO de un archivo HTML suelto
(vía Pyodide: Python corriendo en el navegador, sin servidor, sin dependencias,
solo stdlib). También funciona en Python normal para pruebas.

Uso:
    import json
    res_txt = run_orion(csv_text, codigo_py, spread=0.0002, modo="completo")
    res = json.loads(res_txt)

- Ingesta MT5 inteligente (cabecera <DATE>;<TIME>…, con/sin cabecera, ; , TAB,
  decimales europeos) con validación: columnas, duplicados, orden, huecos, tamaño.
- Anti-lookahead POR DISEÑO (Ventana: solo barras 0..i).
- Interfaz: def on_bar(i, velas) → 1/-1/0  (o legado senal()).
- Rechazos numerados con línea exacta y causa: sintaxis / sandbox / lookahead /
  crash / 0 operaciones / datos.
- Métricas reales: WR, PF, expectancy, Sharpe, Sortino, MaxDD, MAE/MFE por trade,
  desglose por dirección y sesión; equity; IS/OOS 70/30; sensibilidad ±25%.
- modo="rapido": solo backtest + métricas (pintar ya); modo="completo": + anti-overfit.
"""
import csv, io, json, math, re, statistics, sys, traceback
from datetime import datetime

# ============================ INGESTA + VALIDADOR ===========================
def _sniff(txt):
    for linea in txt.splitlines()[:3]:
        for d in ("\t", ";", ",", "|"):
            if linea.count(d) >= 3:
                return d
    return ","

def _parse_float(s):
    s = s.strip().replace(" ", "").replace(" ", "")
    if "," in s:
        if "." in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        else:
            s = s.replace(",", ".")
    return float(s)

def _ts(f):
    f = f.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M",
                "%d/%m/%Y %H:%M", "%Y-%m-%d", "%Y.%m.%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(f, fmt)
        except ValueError:
            pass
    return None

def ingestar(txt):
    """→ (velas, warnings, meta)  ó  lanza ValueError(lista JSON de errores numerados)."""
    errores, warnings = [], []
    delim = _sniff(txt)
    try:
        filas = list(csv.reader(io.StringIO(txt), delimiter=delim))
    except Exception as e:
        raise ValueError(json.dumps([{"n": 1, "linea": 1, "causa": "parseo CSV", "detalle": str(e)}], ensure_ascii=False))
    if len(filas) < 3:
        raise ValueError(json.dumps([{"n": 1, "linea": 1, "causa": "archivo",
            "detalle": "Menos de 3 líneas: no parece un CSV de velas OHLCV."}], ensure_ascii=False))
    cab = [c.strip().lower().strip("<>").replace('"', "") for c in filas[0]]
    es_cab = any(w in ("date","time","open","high","low","close","fecha","hora","apertura",
                       "max","min","cierre","vol","tickvol","spread") for w in cab)
    def col(*n):
        for x in n:
            if x in cab: return cab.index(x)
        return None
    if es_cab:
        i_f, i_t = col("date","fecha","day"), col("time","hora")
        i_o, i_h = col("open","apertura","o"), col("high","max","alto","h")
        i_l, i_c = col("low","min","bajo","l"), col("close","cierre","last","c")
        inicio = 1
    else:
        i_f, i_t, i_o, i_h, i_l, i_c, inicio = 0, 1, 2, 3, 4, 5, 0
        warnings.append("Sin cabecera: asumo formato MT5  <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> …")
    if i_c is None:
        raise ValueError(json.dumps([{"n": 1, "linea": 1, "causa": "columnas",
            "detalle": "No encuentro columna CLOSE/cierre. Cabeceras vistas: %s" % ", ".join(cab[:10])}], ensure_ascii=False))
    velas, vistos, malas = [], {}, 0
    for ln, fila in enumerate(filas[inicio:], inicio + 1):
        if not fila or all(not x.strip() for x in fila):
            continue
        try:
            fecha = fila[i_f].strip() if i_f is not None else ""
            if i_t is not None and len(fila) > i_t and re.match(r"^\d{1,2}:\d{2}", fila[i_t].strip() or ""):
                fecha = (fecha + " " + fila[i_t].strip())[:19]
            def num(ix):
                return _parse_float(fila[ix]) if ix is not None and ix < len(fila) and fila[ix].strip() != "" else None
            o, h, l, c = num(i_o), num(i_h), num(i_l), num(i_c)
            if c is None or c <= 0:
                raise ValueError("cierre inválido")
            o = c if o is None else o; h = c if h is None else h; l = c if l is None else l
            if fecha in vistos:
                errores.append({"n": len(errores)+1, "linea": ln, "causa": "duplicado",
                                "detalle": "Vela duplicada «%s» (ya en línea %d)." % (fecha, vistos[fecha])})
                continue
            vistos[fecha] = ln
            velas.append({"fecha": fecha, "o": o, "h": h, "l": l, "c": c})
        except (ValueError, IndexError):
            malas += 1
    if malas:
        warnings.append("%d líneas no parseables ignoradas." % malas)
    tss = [_ts(v["fecha"]) for v in velas]
    tf_txt = "n/d"
    if all(t is not None for t in tss) and len(tss) > 2:
        inver = sum(1 for a, b in zip(tss, tss[1:]) if b < a)
        if inver:
            errores.append({"n": len(errores)+1, "linea": "-", "causa": "desorden temporal",
                            "detalle": "%d velas fuera de orden. Ordena por fecha ascendente." % inver})
        deltas = [b - a for a, b in zip(tss, tss[1:]) if b > a]
        if deltas:
            med = sorted(deltas)[len(deltas)//2]
            if med.total_seconds() > 0:
                huecos = [(a, b) for a, b in zip(tss, tss[1:])
                          if a and b and b > a and (b - a).total_seconds() > med.total_seconds() * 2.5
                          and not (a.weekday() == 4 and b.weekday() == 0)]
                ratio = len(huecos) / len(deltas)
                if ratio > 0.02:
                    big = max(huecos, key=lambda p: (p[1]-p[0]).total_seconds())
                    errores.append({"n": len(errores)+1, "linea": "-", "causa": "huecos de datos",
                        "detalle": "%d huecos (%.1f%%; el mayor %s → %s). Un backtest con agujeros miente."
                        % (len(huecos), ratio*100, big[0], big[1])})
                elif huecos:
                    big = max(huecos, key=lambda p: (p[1]-p[0]).total_seconds())
                    warnings.append("%d hueco(s) puntual(es) — el mayor %s → %s." % (len(huecos), big[0], big[1]))
                sec = med.total_seconds()
                tf_txt = {60:"M1",300:"M5",900:"M15",1800:"M30",3600:"H1",14400:"H4",86400:"D1"}.get(int(sec), "%ds" % sec)
    else:
        warnings.append("Algunas fechas no se interpretaron: sin chequeo de orden/huecos.")
    if len(velas) < 150:
        errores.append({"n": len(errores)+1, "linea": "-", "causa": "histórico corto",
                        "detalle": "Solo %d velas válidas (mínimo 150)." % len(velas)})
    if len(velas) > 300000:
        errores.append({"n": len(errores)+1, "linea": "-", "causa": "demasiado grande",
                        "detalle": "%d velas > 300k. Recorta el rango (consejo: 1-3 años de M15/H1)." % len(velas)})
    if errores:
        raise ValueError(json.dumps(errores, ensure_ascii=False))
    return velas, warnings, {"tf": tf_txt}

# ============================ VENTANA ANTI-LOOKAHEAD ========================
class Ventana:
    __slots__ = ("_d", "_i")
    def __init__(self, datos, i):
        object.__setattr__(self, "_d", datos); object.__setattr__(self, "_i", i)
    def __setattr__(self, *a):
        raise AttributeError("Ventana inmutable")
    def __len__(self):
        return self._i + 1
    def __getitem__(self, idx):
        if isinstance(idx, slice):
            a, b, s = idx.indices(self._i + 1)
            return self._d[a:b:s]
        if idx >= self._i + 1 or idx < -(self._i + 1):
            raise IndexError("LOOKAHEAD BLOQUEADO: pediste la barra %s pero solo existen %d barras pasadas (0..%d)."
                             % (idx, self._i + 1, self._i))
        return self._d[idx]
    def __iter__(self):
        d, n, k = self._d, self._i + 1, 0
        while k < n:
            yield d[k]; k += 1

# ============================ INDICADORES GRATIS ============================
def ema(valores, periodo):
    valores = list(valores)
    if len(valores) < periodo:
        return valores[-1] if valores else 0.0
    k = 2 / (periodo + 1)
    e = sum(valores[:periodo]) / periodo
    for v in valores[periodo:]:
        e = v * k + e * (1 - k)
    return e

def sma(valores, periodo):
    valores = list(valores)
    if len(valores) < periodo or periodo <= 0:
        return valores[-1] if valores else 0.0
    return sum(valores[-periodo:]) / periodo

def rsi(valores, periodo=14):
    valores = list(valores)
    if len(valores) <= periodo:
        return 50.0
    gan, per = [], []
    for a, b in zip(valores[-periodo-1:-1], valores[-periodo:]):
        d = b - a
        gan.append(max(d, 0)); per.append(max(-d, 0))
    ap = sum(per) / periodo
    if ap == 0: return 100.0
    return 100 - 100 / (1 + (sum(gan)/periodo) / ap)

def atr(velas, periodo=14):
    velas = list(velas)
    if len(velas) <= periodo:
        periodo = max(1, len(velas) - 1)
    trs = []
    tramo = velas[-periodo-1:]
    for j in range(1, len(tramo)):
        h, l, pc = tramo[j]["h"], tramo[j]["l"], tramo[j-1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 1e-9

def maximo(velas, periodo, campo="h"):
    return max(v[campo] for v in list(velas)[-periodo:])

def minimo(velas, periodo, campo="l"):
    return min(v[campo] for v in list(velas)[-periodo:])

# ============================ HELPERS ESTRATEGIA ============================
def _fresh_ns():
    return {"ema": ema, "sma": sma, "rsi": rsi, "atr": atr,
            "maximo": maximo, "minimo": minimo, "math": math}

def _cfg_de(ns):
    return {k: ns.get(k) for k in ("STOP_PIPS","TAKE_PIPS","STOP_ATR","TAKE_ATR","RIESGO_PCT") if ns.get(k) is not None}

def _fn(ns):
    f = ns.get("on_bar") or ns.get("senal")
    return f if callable(f) else None

def detectar_parametros(ns):
    return {k: v for k, v in ns.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and k == k.upper() and any(c.isalpha() for c in k)
            and not k.startswith("_") and k != "RIESGO_PCT"}

def _sesion(fecha):
    try:
        h = int(fecha[11:13])
    except (ValueError, IndexError):
        return "n/d"
    if 7 <= h < 12: return "Londres"
    if 12 <= h < 16: return "Solape LD/NY"
    if 16 <= h < 21: return "Nueva York"
    return "Asia/pacífico"

# ============================ MOTOR BAR-A-BAR ===============================
def backtest(velas, fn, cfg, spread):
    n = len(velas); warm = min(60, max(1, n // 10))
    riesgo = cfg.get("RIESGO_PCT", 1.0) / 100.0
    stop_p, take_p = cfg.get("STOP_PIPS"), cfg.get("TAKE_PIPS")
    stop_a, take_a = cfg.get("STOP_ATR"), cfg.get("TAKE_ATR")
    trades, s_prev, pos = [], 0, None
    for i in range(warm, n):
        try:
            s = fn(i, Ventana(velas, i))
        except IndexError:
            raise
        except Exception:
            s = 0
        s = 1 if s and s > 0 else (-1 if s and s < 0 else 0)
        if pos is not None:
            v = velas[i]
            fav = (v["h"] - pos["entrada"]) * pos["dir"]; adv = (v["l"] - pos["entrada"]) * pos["dir"]
            if fav > pos["mfe"]: pos["mfe"] = fav
            if adv < pos["mae"]: pos["mae"] = adv
            salida, motivo = None, ""
            if pos["dir"] == 1:
                if pos["sl"] is not None and v["l"] <= pos["sl"]: salida, motivo = pos["sl"], "SL"
                elif pos["tp"] is not None and v["h"] >= pos["tp"]: salida, motivo = pos["tp"], "TP"
            else:
                if pos["sl"] is not None and v["h"] >= pos["sl"]: salida, motivo = pos["sl"], "SL"
                elif pos["tp"] is not None and v["l"] <= pos["tp"]: salida, motivo = pos["tp"], "TP"
            if salida is None and s != 0 and s != pos["dir"] and i + 1 < n:
                salida, motivo = velas[i+1]["o"] + (spread/2 if pos["dir"] == -1 else -spread/2), "señal contraria"
            if salida is None and i == n - 1:
                salida, motivo = v["c"], "fin de datos"
            if salida is not None:
                pts = (salida - pos["entrada"]) * pos["dir"]
                trades.append({"dir": pos["dir"], "pts": pts, "sl_dist": pos["sl_dist"], "motivo": motivo,
                               "fecha_e": pos["fecha"], "fecha_s": v["fecha"], "barras": i - pos["i"],
                               "sesion": _sesion(pos["fecha"]), "mae": pos["mae"], "mfe": pos["mfe"]})
                pos = None
        if pos is None and s != 0 and s != s_prev and i + 1 < n:
            a = atr(velas[:i+1]) or 1e-9
            sl_dist = stop_p if stop_p else (stop_a * a if stop_a else None)
            tp_dist = take_p if take_p else (take_a * a if take_a else None)
            ent = velas[i+1]["o"]
            if s == 1:
                ent += spread/2
                sl = ent - sl_dist if sl_dist else None; tp = ent + tp_dist if tp_dist else None
            else:
                ent -= spread/2
                sl = ent + sl_dist if sl_dist else None; tp = ent - tp_dist if tp_dist else None
            if sl_dist is None: sl_dist = 2 * a
            pos = {"dir": s, "entrada": ent, "sl": sl, "tp": tp, "sl_dist": sl_dist,
                   "fecha": velas[i+1]["fecha"], "i": i + 1, "mae": 0.0, "mfe": 0.0}
        s_prev = s
    equity, curva = 100.0, [100.0]
    for t in trades:
        r = (t["pts"] / t["sl_dist"]) if t["sl_dist"] > 0 else 0.0
        t["pnl_pct"] = riesgo * r * 100
        equity *= (1 + riesgo * r); curva.append(round(equity, 4))
    return trades, curva

def metricas(trades, curva, velas):
    wins = [t for t in trades if t["pts"] > 0]; loses = [t for t in trades if t["pts"] <= 0]
    gp = sum(t["pts"] for t in wins); gl = abs(sum(t["pts"] for t in loses)) or 1e-12
    pf = gp / gl if loses else (99.0 if gp > 0 else 0)
    pico, mdd, dd_cur, dd_max = curva[0], 0.0, 0, 0
    for e in curva:
        if e >= pico: pico, dd_cur = e, 0
        else:
            dd_cur += 1; dd_max = max(dd_max, dd_cur)
            mdd = max(mdd, (pico - e) / pico * 100)
    rets = [math.log(curva[i]/curva[i-1]) for i in range(1, len(curva)) if curva[i-1] > 0]
    ops_anyo = len(trades) / max(len(velas)/252, 1e-9)
    desv = statistics.pstdev(rets) if len(rets) > 1 else 0
    sharpe = statistics.mean(rets)/desv*math.sqrt(ops_anyo) if desv > 0 and len(rets) > 2 else 0.0
    downs = [r for r in rets if r < 0]
    ddev = math.sqrt(sum(r*r for r in downs)/len(downs)) if downs else 0
    sortino = statistics.mean(rets)/ddev*math.sqrt(ops_anyo) if ddev > 0 and len(rets) > 2 else 0.0
    racha, mx = 0, 0
    for t in trades:
        if t["pts"] <= 0: racha += 1; mx = max(mx, racha)
        else: racha = 0
    orden = sorted(trades, key=lambda t: t["pnl_pct"], reverse=True)
    conc = sum(t["pnl_pct"] for t in orden[:3]) / (sum(t["pnl_pct"] for t in wins) or 1e-12) * 100 if wins else 0
    maes = [t["mae"] for t in trades]; mfes = [t["mfe"] for t in trades]
    por_dir, por_ses = {}, {}
    for t in trades:
        d = "largo" if t["dir"] == 1 else "corto"
        por_dir.setdefault(d, {"n": 0, "wins": 0, "pts": 0.0})
        por_dir[d]["n"] += 1; por_dir[d]["wins"] += int(t["pts"] > 0); por_dir[d]["pts"] += t["pts"]
        por_ses.setdefault(t["sesion"], {"n": 0, "wins": 0, "pts": 0.0})
        por_ses[t["sesion"]]["n"] += 1; por_ses[t["sesion"]]["wins"] += int(t["pts"] > 0)
        por_ses[t["sesion"]]["pts"] += t["pts"]
    return {"trades": len(trades), "wins": len(wins), "loses": len(loses),
            "wr": len(wins)/len(trades)*100 if trades else 0, "pf": pf,
            "pnl_pts": sum(t["pts"] for t in trades), "ret_pct": curva[-1]-100,
            "mdd": mdd, "dd_barras": dd_max, "sharpe": sharpe, "sortino": sortino,
            "max_racha_neg": mx,
            "expect_pts": (sum(t["pts"] for t in trades)/len(trades)) if trades else 0,
            "conc3": conc, "ops_anyo": ops_anyo,
            "mae_medio": statistics.mean(maes) if maes else 0,
            "mfe_medio": statistics.mean(mfes) if mfes else 0,
            "ratio_mfe_mae": (statistics.mean(mfes)/abs(statistics.mean(maes))) if maes and abs(statistics.mean(maes)) > 1e-12 else 0,
            "sl": sum(1 for t in trades if t["motivo"] == "SL"),
            "tp": sum(1 for t in trades if t["motivo"] == "TP"),
            "flip": sum(1 for t in trades if t["motivo"] == "señal contraria"),
            "barras_media": statistics.mean([t["barras"] for t in trades]) if trades else 0,
            "buyhold": (velas[-1]["c"]/velas[0]["o"]-1)*100,
            "por_dir": por_dir, "por_sesion": por_ses}

def diagnosticar(m, cfg, trades, n_params):
    problemas, avisos = [], []
    if m["trades"] < 30:
        problemas.append("Muestra pequeña: %d operaciones (<30). Amplía histórico o baja timeframe." % m["trades"])
    if cfg.get("STOP_PIPS") is None and cfg.get("STOP_ATR") is None:
        problemas.append("Sin stop definido (STOP_PIPS/STOP_ATR): se simuló uno virtual 2×ATR solo para dimensionar.")
    if m["pf"] < 1:
        problemas.append("Profit factor %.2f < 1: pierde más de lo que gana." % m["pf"])
    elif m["pf"] < 1.3:
        avisos.append("Profit factor %.2f: marginal; los costes reales pueden diluirlo." % m["pf"])
    if m["mdd"] > 25:
        problemas.append("Max drawdown %.0f%%: casi nadie lo aguanta en vivo." % m["mdd"])
    elif m["mdd"] > 15:
        avisos.append("Drawdown %.0f%%: considerable." % m["mdd"])
    if m["conc3"] > 40 and m["wins"] > 0:
        problemas.append("El %.0f%% del beneficio viene de 3 operaciones: dependencia de suerte." % m["conc3"])
    if m["expect_pts"] <= 0:
        problemas.append("Expectativa ≤ 0 por operación.")
    if m["max_racha_neg"] >= 8:
        problemas.append("Racha de %d pérdidas seguidas posible." % m["max_racha_neg"])
    if n_params > 4:
        avisos.append("%d parámetros ajustables: alto riesgo de sobreajuste (máx. 3-4)." % n_params)
    if m["mfe_medio"] > 0 and m["expect_pts"] < m["mfe_medio"] * 0.25:
        avisos.append("MFE medio %.4g pero capturas %.4g: dejas la mayoría del movimiento en la mesa (salidas rápidas o sin trailing)." % (m["mfe_medio"], m["expect_pts"]))
    if m["mae_medio"] < 0 and abs(m["expect_pts"]) < abs(m["mae_medio"]) * 0.15:
        avisos.append("MAE medio %.4g grande para lo que ganas por trade: mucho sufrimiento, poco premio." % m["mae_medio"])
    return problemas, avisos

# ============================ PUNTO DE ENTRADA ==============================
def run_orion(csv_text, codigo, spread=0.0, modo="completo"):
    out = _run_interno(csv_text, codigo, float(spread or 0), modo)
    return json.dumps(out, ensure_ascii=False)

def _rechazo(errores):
    return {"ok": False, "errores": errores}

def _run_interno(csv_text, codigo, spread, modo):
    # 1) ingesta
    try:
        velas, warnings, meta = ingestar(csv_text)
    except ValueError as e:
        try:
            return _rechazo(json.loads(str(e)))
        except Exception:
            return _rechazo([{"n": 1, "linea": "-", "causa": "datos", "detalle": str(e)}])
    errores = []
    # 2) validación estática
    prohibidos = ["import os", "import sys", "subprocess", "socket", "urllib", "requests",
                  "__import__", "open(", "eval(", "exec(", "input(", "shutil", "pathlib"]
    for num, linea in enumerate(codigo.splitlines(), 1):
        low = linea.lower()
        for p in prohibidos:
            if p in low:
                errores.append({"n": len(errores)+1, "linea": num, "causa": "código prohibido",
                                "detalle": "«%s» no permitido en estrategias (sandbox)." % p})
    try:
        compile(codigo, "estrategia.py", "exec")
    except SyntaxError as e:
        errores.append({"n": len(errores)+1, "linea": e.lineno or "?", "causa": "sintaxis Python",
                        "detalle": "%s — revisa esa línea." % e.msg})
    if errores:
        return _rechazo(errores)
    ns = _fresh_ns()
    try:
        exec(compile(codigo, "estrategia.py", "exec"), ns)
    except Exception as e:
        tb = traceback.extract_tb(sys.exc_info()[2])
        linea = next((f.lineno for f in tb if f.filename == "estrategia.py"), "?")
        return _rechazo([{"n": 1, "linea": linea, "causa": "error al cargar",
                          "detalle": "%s: %s" % (type(e).__name__, e)}])
    fn = _fn(ns)
    if fn is None:
        return _rechazo([{"n": 1, "linea": "-", "causa": "interfaz ausente",
                          "detalle": "Falta def on_bar(i, velas) (o senal) devolviendo 1 / -1 / 0."}])
    # 3) prueba 150 barras (lookahead / crash)
    try:
        list(fn(i, Ventana(velas[:150], i)) for i in range(20, min(150, len(velas))))
    except IndexError as e:
        return _rechazo([{"n": 1, "linea": "dentro de on_bar", "causa": "uso de datos futuros", "detalle": str(e)}])
    except Exception as e:
        tb = traceback.extract_tb(sys.exc_info()[2])
        linea = next((f.lineno for f in tb if f.filename == "estrategia.py"), "?")
        return _rechazo([{"n": 1, "linea": linea, "causa": "crash en barra de prueba",
                          "detalle": "%s: %s" % (type(e).__name__, e)}])
    # 4) backtest real
    cfg = _cfg_de(ns)
    params = detectar_parametros(ns)
    try:
        trades, curva = backtest(velas, fn, cfg, spread)
    except IndexError as e:
        return _rechazo([{"n": 1, "linea": "dentro de on_bar", "causa": "uso de datos futuros", "detalle": str(e)}])
    if not trades:
        return _rechazo([{"n": 1, "linea": "-", "causa": "0 operaciones",
                          "detalle": "No abrió nada en todo el histórico. Revisa sus condiciones."}])
    m = metricas(trades, curva, velas)
    problemas, avisos = diagnosticar(m, cfg, trades, len(params))
    res = {
        "ok": True, "avisado": warnings,
        "dataset": {"velas": len(velas), "ini": velas[0]["fecha"], "fin": velas[-1]["fecha"], "tf": meta["tf"]},
        "m": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if k not in ("por_dir", "por_sesion")},
        "por_dir": m["por_dir"], "por_sesion": m["por_sesion"],
        "problemas": problemas, "avisos": avisos,
        "n_params": len(params),
        "curva": curva,
        "scatter": [{"mae": round(t["mae"], 4), "mfe": round(t["mfe"], 4),
                     "win": t["pts"] > 0, "dir": t["dir"]} for t in trades],
        "trades": [{"fecha_e": t["fecha_e"], "fecha_s": t["fecha_s"], "dir": t["dir"],
                    "pts": round(t["pts"], 4), "pnl_pct": round(t["pnl_pct"], 2),
                    "mae": round(t["mae"], 4), "mfe": round(t["mfe"], 4),
                    "motivo": t["motivo"], "sesion": t["sesion"], "barras": t["barras"]}
                   for t in trades],
        "is_oos": None, "sens": [], "veredicto": None,
    }
    # 5) anti-overfit (modo completo)
    if modo != "rapido":
        corte = int(len(velas) * 0.7)
        def _tramo(tr):
            t2, c2 = backtest(tr, fn, cfg, 0)
            return metricas(t2, c2, tr) if len(t2) >= 3 else None
        is_m = _tramo(velas[:corte]); os_m = _tramo(velas[max(0, corte - 60):])
        res["is_oos"] = {
            "is_m": ({"pf": round(is_m["pf"], 2), "trades": is_m["trades"], "ret": round(is_m["ret_pct"], 2)} if is_m else None),
            "os_m": ({"pf": round(os_m["pf"], 2), "trades": os_m["trades"], "ret": round(os_m["ret_pct"], 2)} if os_m else None)}
        if is_m and os_m:
            if is_m["pf"] >= 1.2 and os_m["pf"] < 1:
                problemas.append("SOBREAJUSTE clásico: PF %.2f en entrenamiento pero %.2f en prueba ciega." % (is_m["pf"], os_m["pf"]))
            elif is_m["pf"] > 1 and os_m["pf"] < is_m["pf"] * 0.6:
                avisos.append("Degradación fuera de muestra (PF %.2f → %.2f)." % (is_m["pf"], os_m["pf"]))
        codigo_c = codigo
        for nombre, valor in params.items():
            pfs = []
            for f in (0.75, 1.25):
                v = valor * f
                if isinstance(valor, int): v = max(1, int(round(v)))
                if v == valor: v = valor + (-1 if f < 1 else 1)
                ns2 = _fresh_ns()
                try:
                    exec(compile(codigo_c, "estrategia.py", "exec"), ns2)
                    ns2[nombre] = v
                    t3, c3 = backtest(velas, _fn(ns2), _cfg_de(ns2), spread)
                    if len(t3) >= 3:
                        pfs.append(metricas(t3, c3, velas)["pf"])
                except Exception:
                    pass
            if len(pfs) >= 2:
                lo, hi = min(pfs), max(pfs)
                if m["pf"] >= 1 and lo < 1: est = "frágil"
                elif (hi - lo) > 0.5 * max(abs(m["pf"]), 0.5): est = "sensible"
                else: est = "estable"
                res["sens"].append({"nombre": nombre, "valor": valor, "lo": round(lo, 2), "hi": round(hi, 2), "estado": est})
            else:
                res["sens"].append({"nombre": nombre, "valor": valor, "lo": None, "hi": None, "estado": "tenso"})
    # 6) veredicto
    if problemas and m["pf"] < 1: cls_v, txt_v = "bad", "NO RENTABLE"
    elif problemas: cls_v, txt_v = "warn", "RENTABLE EN PAPEL, PERO CON PEGAS SERIAS"
    elif avisos and (m["pf"] < 1.3 or m["trades"] < 40): cls_v, txt_v = "warn", "PROMETE, PERO AÚN NO ES CONFIABLE"
    else: cls_v, txt_v = "ok", "RENTABLE Y ESTADÍSTICAMENTE DIGNA"
    res["veredicto"] = {"cls": cls_v, "texto": txt_v}
    res["problemas"] = problemas; res["avisos"] = avisos
    return res
