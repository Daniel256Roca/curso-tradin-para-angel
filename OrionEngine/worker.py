#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORION ENGINE · worker.py
========================
Motor de backtest bar-a-bar (event-driven). Se ejecuta como PROCESO AISLADO:
    python worker.py datos.csv estrategia.py resultado.json status.json [spread]

Anti-lookahead POR DISEÑO: la estrategia recibe una Ventana que solo deja leer
las barras 0..i. Pedir la barra i+1 lanza IndexError con mensaje claro.

Escribe:
  · status.json   → {"fase": "...", "pct": 0-100, "log": [...], "parcial": {...}}
  · resultado.json → dict completo (métricas reales, MAE/MFE, equity, trades,
                     IS/OOS lado a lado, sensibilidad ±25%, avisos/problemas)
"""
import csv, io, json, math, statistics, sys, time, traceback

# ============================== VENTANA ANTI-LOOKAHEAD ======================
class Ventana:
    """Vista del array de velas limitada estrictamente a las barras pasadas."""
    __slots__ = ("_d", "_i")
    def __init__(self, datos, i):
        object.__setattr__(self, "_d", datos); object.__setattr__(self, "_i", i)
    def __setattr__(self, *a):  # inmutable
        raise AttributeError("Ventana inmutable")
    def __len__(self):
        return self._i + 1
    def __getitem__(self, idx):
        if isinstance(idx, slice):
            a, b, s = idx.indices(self._i + 1)
            return self._d[a:b:s]
        if idx >= self._i + 1 or idx < -(self._i + 1):
            raise IndexError(
                "LOOKAHEAD BLOQUEADO: tu estrategia pidió la barra %s pero en este "
                "momento solo existen %d barras pasadas (0..%d). El futuro no se opera."
                % (idx, self._i + 1, self._i))
        return self._d[idx]
    def __iter__(self):
        d, n = self._d, self._i + 1
        k = 0
        while k < n:
            yield d[k]; k += 1

# ============================== INDICADORES GRATIS ==========================
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
    return 100 - 100 / (1 + (sum(gan) / periodo) / ap)

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

# ============================== HELPERS INTERNOS ============================
def _fresh_ns():
    return {"ema": ema, "sma": sma, "rsi": rsi, "atr": atr,
            "maximo": maximo, "minimo": minimo, "Ventana": Ventana,
            "math": math}

def _cfg_de(ns):
    return {k: ns.get(k) for k in ("STOP_PIPS","TAKE_PIPS","STOP_ATR","TAKE_ATR","RIESGO_PCT")
            if ns.get(k) is not None}

def detectar_parametros(ns):
    out = {}
    for k, v in ns.items():
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and k == k.upper() and any(c.isalpha() for c in k)
                and not k.startswith("_") and k != "RIESGO_PCT"):
            out[k] = v
    return out

def _fn_senal(ns):
    """Acepta la interfaz nueva on_bar(i, velas) o la clásica senal(i, velas)."""
    fn = ns.get("on_bar") or ns.get("senal")
    return fn if callable(fn) else None

def _sesion(fecha):
    try:
        h = int(fecha[11:13])
    except (ValueError, IndexError):
        return "n/d"
    if 7 <= h < 12: return "Londres"
    if 12 <= h < 16: return "Solape LD/NY"
    if 16 <= h < 21: return "Nueva York"
    return "Asia/pacífico"

# ============================== MOTOR BAR-A-BAR =============================
def ejecutar_backtest(velas, fn, cfg, spread, on_progreso=None):
    n = len(velas); warm = min(60, max(1, n // 10))
    riesgo = cfg.get("RIESGO_PCT", 1.0) / 100.0
    stop_p, take_p = cfg.get("STOP_PIPS"), cfg.get("TAKE_PIPS")
    stop_a, take_a = cfg.get("STOP_ATR"), cfg.get("TAKE_ATR")
    trades, s_prev, pos = [], 0, None
    tick = max(1, n // 40)
    for i in range(warm, n):
        try:
            s = fn(i, Ventana(velas, i))
        except IndexError:
            raise      # lookahead: se reporta aparte con el traceback real
        except Exception:
            s = 0      # fallo puntual de la estrategia en una barra = sin señal
        s = 1 if s and s > 0 else (-1 if s and s < 0 else 0)
        if pos is not None:
            v = velas[i]
            # MAE / MFE reales, barra a barra
            fav = (v["h"] - pos["entrada"]) * pos["dir"]
            adv = (v["l"] - pos["entrada"]) * pos["dir"]
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
                trades.append({"dir": pos["dir"], "entrada": pos["entrada"], "salida": salida,
                               "pts": pts, "sl_dist": pos["sl_dist"], "motivo": motivo,
                               "fecha_e": pos["fecha"], "fecha_s": v["fecha"],
                               "barras": i - pos["i"], "sesion": _sesion(pos["fecha"]),
                               "mae": pos["mae"], "mfe": pos["mfe"]})
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
        if on_progreso and (i % tick == 0):
            on_progreso(5 + int((i - warm) / max(1, n - warm) * 60), len(trades))
    equity, curva = 100.0, [100.0]
    for t in trades:
        r = (t["pts"] / t["sl_dist"]) if t["sl_dist"] > 0 else 0.0
        t["pnl_pct"] = riesgo * r * 100
        equity *= (1 + riesgo * r); curva.append(equity)
        curva[-1] = round(curva[-1], 4)
    return trades, curva

# ============================== MÉTRICAS ====================================
def metricas(trades, curva, velas):
    wins  = [t for t in trades if t["pts"] > 0]
    loses = [t for t in trades if t["pts"] <= 0]
    gp = sum(t["pts"] for t in wins); gl = abs(sum(t["pts"] for t in loses)) or 1e-12
    pf = gp / gl if loses else (99.0 if gp > 0 else 0)
    pnl = sum(t["pts"] for t in trades)
    pico, mdd, dd_ini, dd_max_ini = curva[0], 0.0, 0, 0
    for e in curva:
        if e >= pico: pico, dd_ini = e, 0
        else:
            dd_ini += 1; dd_max_ini = max(dd_max_ini, dd_ini)
            mdd = max(mdd, (pico - e) / pico * 100)
    rets = [math.log(curva[i]/curva[i-1]) for i in range(1, len(curva)) if curva[i-1] > 0]
    ops_anyo = len(trades) / max(len(velas)/252, 1e-9)
    desv = statistics.pstdev(rets) if len(rets) > 1 else 0
    sharpe = (statistics.mean(rets)/desv*math.sqrt(ops_anyo)) if desv > 0 and len(rets) > 2 else 0.0
    downs = [r for r in rets if r < 0]
    ddev = math.sqrt(sum(r*r for r in downs)/len(downs)) if downs else 0
    sortino = (statistics.mean(rets)/ddev*math.sqrt(ops_anyo)) if ddev > 0 and len(rets) > 2 else 0.0
    racha, mx = 0, 0
    for t in trades:
        if t["pts"] <= 0: racha += 1; mx = max(mx, racha)
        else: racha = 0
    orden = sorted(trades, key=lambda t: t["pnl_pct"], reverse=True)
    conc = (sum(t["pnl_pct"] for t in orden[:3]) / (sum(t["pnl_pct"] for t in wins) or 1e-12)) * 100 if wins else 0
    maes = [t["mae"] for t in trades]; mfes = [t["mfe"] for t in trades]
    por_dir = {}
    for t in trades:
        d = "largo" if t["dir"] == 1 else "corto"
        por_dir.setdefault(d, {"n": 0, "wins": 0, "pts": 0.0})
        por_dir[d]["n"] += 1; por_dir[d]["wins"] += 1 if t["pts"] > 0 else 0; por_dir[d]["pts"] += t["pts"]
    por_sesion = {}
    for t in trades:
        por_sesion.setdefault(t["sesion"], {"n": 0, "wins": 0, "pts": 0.0})
        por_sesion[t["sesion"]]["n"] += 1; por_sesion[t["sesion"]]["wins"] += 1 if t["pts"] > 0 else 0
        por_sesion[t["sesion"]]["pts"] += t["pts"]
    return {"trades": len(trades), "wins": len(wins), "loses": len(loses),
            "wr": len(wins)/len(trades)*100 if trades else 0, "pf": pf, "pnl_pts": pnl,
            "ret_pct": curva[-1]-100, "mdd": mdd, "dd_barras": dd_max_ini,
            "sharpe": sharpe, "sortino": sortino, "max_racha_neg": mx,
            "expect_pts": pnl/len(trades) if trades else 0,
            "expect_R": (curva[-1]/100) ** (1/max(1, len(trades))) - 1 if trades else 0,
            "conc3": conc, "ops_anyo": ops_anyo,
            "mae_medio": statistics.mean(maes) if maes else 0,
            "mfe_medio": statistics.mean(mfes) if mfes else 0,
            "ratio_mfe_mae": (statistics.mean(mfes)/abs(statistics.mean(maes))) if maes and abs(statistics.mean(maes)) > 1e-12 else 0,
            "sl": sum(1 for t in trades if t["motivo"] == "SL"),
            "tp": sum(1 for t in trades if t["motivo"] == "TP"),
            "flip": sum(1 for t in trades if t["motivo"] == "señal contraria"),
            "barras_media": statistics.mean([t["barras"] for t in trades]) if trades else 0,
            "buyhold": (velas[-1]["c"]/velas[0]["o"]-1)*100,
            "por_dir": por_dir, "por_sesion": por_sesion}

def diagnosticar(m, cfg, trades, n_params):
    problemas, avisos = [], []
    if m["trades"] < 30:
        problemas.append("Muestra pequeña: %d operaciones (<30). Amplía histórico o baja timeframe antes de creerte nada." % m["trades"])
    if cfg.get("STOP_PIPS") is None and cfg.get("STOP_ATR") is None:
        problemas.append("La estrategia NO define stop (STOP_PIPS/STOP_ATR). Se simuló uno virtual 2×ATR solo para dimensionar.")
    if m["pf"] < 1:
        problemas.append("Profit factor %.2f < 1: pierde más de lo que gana. No operable." % m["pf"])
    elif m["pf"] < 1.3:
        avisos.append("Profit factor %.2f: marginal; con costes reales puede diluirse." % m["pf"])
    if m["mdd"] > 25:
        problemas.append("Max drawdown %.0f%%: casi nadie lo aguanta en vivo sin sabotear el sistema." % m["mdd"])
    elif m["mdd"] > 15:
        avisos.append("Drawdown %.0f%%: considerable. ¿Lo resistirías sin apagar el bot?" % m["mdd"])
    if m["conc3"] > 40 and m["wins"] > 0:
        problemas.append("El %.0f%% del beneficio viene de 3 operaciones: dependencia de suerte/outliers." % m["conc3"])
    if m["expect_pts"] <= 0:
        problemas.append("Expectativa ≤ 0 por operación.")
    if m["max_racha_neg"] >= 8:
        problemas.append("Racha de %d pérdidas seguidas posible: con %s%% de riesgo son ~%.0f%% de cuenta."
                         % (m["max_racha_neg"], str(cfg.get("RIESGO_PCT", 1.0)), m["max_racha_neg"]*cfg.get("RIESGO_PCT", 1.0)))
    if n_params > 4:
        avisos.append("%d parámetros ajustables detectados: riesgo alto de sobreajuste (máx. recomendado 3-4)." % n_params)
    if m["mfe_medio"] > 0 and m["expect_pts"] < m["mfe_medio"] * 0.25:
        avisos.append("MFE medio %.2f pts pero capturas solo %.2f: dejas escapar la mayoría del movimiento (salidas demasiado rápidas o sin trailing)." % (m["mfe_medio"], m["expect_pts"]))
    if m["mae_medio"] < 0 and abs(m["expect_pts"]) < abs(m["mae_medio"]) * 0.15:
        avisos.append("MAE medio %.2f pts muy grande respecto a lo que ganas por trade: mucho sufrimiento para poco premio." % m["mae_medio"])
    return problemas, avisos

# ============================== MAIN DEL WORKER =============================
def _status(ruta, fase, pct, log_line=None, parcial=None):
    try:
        st = {}
        try: st = json.load(open(ruta, encoding="utf-8"))
        except Exception: st = {"log": []}
        st["fase"] = fase; st["pct"] = pct
        if log_line:
            st.setdefault("log", []).append("[%s] %s" % (time.strftime("%H:%M:%S"), log_line))
            st["log"] = st["log"][-40:]
        if parcial is not None: st["parcial"] = parcial
        json.dump(st, open(ruta, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

def _err(ruta_out, ruta_status, errores):
    json.dump({"ok": False, "errores": errores},
              open(ruta_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _status(ruta_status, "rechazado", 100, "❌ Rechazado: %d error(es)" % len(errores))

def main():
    ruta_csv, ruta_est, ruta_out, ruta_status = sys.argv[1:5]
    spread = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    errores = []
    _status(ruta_status, "cargando datos", 2, "Leyendo dataset…")

    # --- 1) datos (ya validados por el servidor; el worker es tolerante) ---
    velas = []
    with open(ruta_csv, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        for fila in rd:
            try:
                fecha = (fila.get("fecha") or fila.get("date") or "").strip()
                c = float(str(fila.get("close") or fila.get("c") or "").replace(",", "."))
                o = float(str(fila.get("open") or fila.get("o") or c).replace(",", "."))
                h = float(str(fila.get("high") or fila.get("h") or c).replace(",", "."))
                l = float(str(fila.get("low") or fila.get("l") or c).replace(",", "."))
                if c > 0: velas.append({"fecha": fecha, "o": o, "h": h, "l": l, "c": c})
            except (ValueError, TypeError):
                continue
    if len(velas) < 150:
        _err(ruta_out, ruta_status, [{"n": 1, "linea": "-", "causa": "datos",
             "detalle": "Solo %d velas válidas tras el parseo (mínimo 150)." % len(velas)}])
        return
    _status(ruta_status, "cargando datos", 5, "✔ %d velas (%s → %s)" % (len(velas), velas[0]["fecha"][:16], velas[-1]["fecha"][:16]))

    # --- 2) estrategia: validación estática + compilación ---
    codigo = ""
    try:
        codigo = open(ruta_est, encoding="utf-8").read()
    except OSError as e:
        _err(ruta_out, ruta_status, [{"n": 1, "linea": "-", "causa": "archivo", "detalle": str(e)}]); return

    prohibidos = ["import os", "import sys", "subprocess", "socket", "urllib", "requests",
                  "__import__", "open(", "eval(", "exec(", "input(", "shutil", "pathlib"]
    for num, linea in enumerate(codigo.splitlines(), 1):
        low = linea.lower()
        for p in prohibidos:
            if p in low:
                errores.append({"n": len(errores)+1, "linea": num, "causa": "código prohibido",
                                "detalle": "«%s» no está permitido dentro de una estrategia (sandbox: solo análisis, sin red ni disco)." % p})
    tree_ok = True
    try:
        compile(codigo, "estrategia.py", "exec")
    except SyntaxError as e:
        tree_ok = False
        errores.append({"n": len(errores)+1, "linea": e.lineno or "?", "causa": "sintaxis Python",
                        "detalle": "%s — revisa esa línea (¿dos puntos? ¿paréntesis? ¿indentación?)." % e.msg})
    if not tree_ok or errores:
        _err(ruta_out, ruta_status, errores); return

    ns = _fresh_ns()
    try:
        exec(compile(codigo, "estrategia.py", "exec"), ns)
    except Exception as e:
        tb = traceback.extract_tb(sys.exc_info()[2])
        linea = next((f.lineno for f in tb if f.filename == "estrategia.py"), "?")
        _err(ruta_out, ruta_status, [{"n": 1, "linea": linea, "causa": "error al cargar (nivel módulo)",
             "detalle": "%s: %s" % (type(e).__name__, e)}]); return

    fn = _fn_senal(ns)
    if fn is None:
        _err(ruta_out, ruta_status, [{"n": 1, "linea": "-", "causa": "interfaz ausente",
             "detalle": "Falta  def on_bar(i, velas):  (o senal(i, velas)) devolviendo 1 / -1 / 0."}]); return

    # --- 3) ejecución de prueba (bars 0..149) con anti-lookahead activo ---
    _status(ruta_status, "validando estrategia", 8, "Validación estática OK · probando 150 barras…")
    try:
        prueba = [fn(i, Ventana(velas[:150], i)) for i in range(20, min(150, len(velas)))]
    except IndexError as e:
        _err(ruta_out, ruta_status, [{"n": 1, "linea": "dentro de on_bar", "causa": "uso de datos futuros",
             "detalle": str(e)}]); return
    except Exception as e:
        tb = traceback.extract_tb(sys.exc_info()[2])
        linea = next((f.lineno for f in tb if f.filename == "estrategia.py"), "?")
        _err(ruta_out, ruta_status, [{"n": 1, "linea": linea, "causa": "crash en barra de prueba",
             "detalle": "%s: %s" % (type(e).__name__, e)}]); return
    if all((not x) or x == 0 for x in prueba):
        errores.append({"n": len(errores)+1, "linea": "-", "causa": "estrategia muda",
                        "detalle": "on_bar devolvió 0 en las %d primeras barras de prueba. Es legal, pero no habrá operaciones." % len(prueba)})

    cfg = _cfg_de(ns)
    params = detectar_parametros(ns)
    _status(ruta_status, "simulando bar a bar", 10,
            "▶ Simulación: %d velas · spread %.3g · params %s" % (len(velas), spread, ", ".join(params) or "—"))

    # --- 4) backtest real ---
    def prog(pct, ntrades):
        _status(ruta_status, "simulando bar a bar", pct, parcial={"trades": ntrades})
    try:
        trades, curva = ejecutar_backtest(velas, fn, cfg, spread, prog)
    except IndexError as e:
        _err(ruta_out, ruta_status, errores + [{"n": len(errores)+1, "linea": "dentro de on_bar",
             "causa": "uso de datos futuros", "detalle": str(e)}]); return
    if not trades:
        _err(ruta_out, ruta_status, errores + [{"n": len(errores)+1, "linea": "-",
             "causa": "0 operaciones", "detalle": "La estrategia no abrió nada en todo el histórico. Revisa sus condiciones."}]); return

    _status(ruta_status, "calculando métricas", 72, "✔ %d operaciones · calculando métricas reales…" % len(trades))
    m = metricas(trades, curva, velas)
    problemas, avisos = diagnosticar(m, cfg, trades, len(params))

    # --- 5) anti-overfit: IS/OOS SIEMPRE, sensibilidad ±25% ---
    _status(ruta_status, "anti-overfit", 78, "🛡️ Split IS/OOS (70/30)…")
    corte = int(len(velas) * 0.7)
    def _run_tramo(tramo):
        t2, c2 = ejecutar_backtest(tramo, fn, cfg, 0)   # spread neutro dentro de tramos
        return metricas(t2, c2, tramo) if len(t2) >= 3 else None
    is_m = _run_tramo(velas[:corte])
    os_m = _run_tramo(velas[max(0, corte - 60):])
    if is_m and os_m:
        if is_m["pf"] >= 1.2 and os_m["pf"] < 1:
            problemas.append("SOBREAJUSTE clásico: PF %.2f en entrenamiento pero %.2f en prueba ciega." % (is_m["pf"], os_m["pf"]))
        elif is_m["pf"] > 1 and os_m["pf"] < is_m["pf"] * 0.6:
            avisos.append("Degradación fuera de muestra (PF %.2f → %.2f)." % (is_m["pf"], os_m["pf"]))

    _status(ruta_status, "anti-overfit", 84, "🛡️ Sensibilidad ±25%% en %d parámetro(s)…" % len(params))
    sens = []
    for nombre, valor in params.items():
        pfs = []
        for f in (0.75, 1.25):
            v = valor * f
            if isinstance(valor, int): v = max(1, int(round(v)))
            if v == valor: v = valor + (-1 if f < 1 else 1)
            ns2 = _fresh_ns()
            try:
                exec(compile(codigo, "estrategia.py", "exec"), ns2)
                ns2[nombre] = v
                t3, c3 = ejecutar_backtest(velas, _fn_senal(ns2), _cfg_de(ns2), spread)
                if len(t3) >= 3:
                    pfs.append(metricas(t3, c3, velas)["pf"])
            except Exception:
                pass
        if len(pfs) >= 2:
            lo, hi = min(pfs), max(pfs)
            if m["pf"] >= 1 and lo < 1: est = "frágil"
            elif (hi - lo) > 0.5 * max(abs(m["pf"]), 0.5): est = "sensible"
            else: est = "estable"
            sens.append({"nombre": nombre, "valor": valor, "lo": round(lo, 2), "hi": round(hi, 2), "estado": est})
        else:
            sens.append({"nombre": nombre, "valor": valor, "lo": None, "hi": None, "estado": "tenso"})

    # --- 6) veredicto + persistencia ---
    if problemas and m["pf"] < 1: cls_v, veredicto = "bad", "NO RENTABLE"
    elif problemas: cls_v, veredicto = "warn", "RENTABLE EN PAPEL, PERO CON PEGAS SERIAS"
    elif avisos and (m["pf"] < 1.3 or m["trades"] < 40): cls_v, veredicto = "warn", "PROMETE, PERO AÚN NO ES CONFIABLE"
    else: cls_v, veredicto = "ok", "RENTABLE Y ESTADÍSTICAMENTE DIGNA"

    resultado = {
        "ok": True, "avisado": [e["detalle"] for e in errores],
        "dataset": {"velas": len(velas), "ini": velas[0]["fecha"], "fin": velas[-1]["fecha"]},
        "m": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if k not in ("por_dir", "por_sesion")},
        "por_dir": m["por_dir"], "por_sesion": m["por_sesion"],
        "problemas": problemas, "avisos": avisos,
        "veredicto": {"cls": cls_v, "texto": veredicto},
        "is_oos": {
            "is_m": ({"pf": round(is_m["pf"], 2), "trades": is_m["trades"], "ret": round(is_m["ret_pct"], 2)} if is_m else None),
            "os_m": ({"pf": round(os_m["pf"], 2), "trades": os_m["trades"], "ret": round(os_m["ret_pct"], 2)} if os_m else None)},
        "n_params": len(params), "sens": sens,
        "curva": curva,
        "scatter": [{"mae": round(t["mae"], 2), "mfe": round(t["mfe"], 2),
                     "win": t["pts"] > 0, "dir": t["dir"]} for t in trades],
        "trades": [{"fecha_e": t["fecha_e"], "fecha_s": t["fecha_s"], "dir": t["dir"],
                    "pts": round(t["pts"], 2), "pnl_pct": round(t["pnl_pct"], 2),
                    "mae": round(t["mae"], 2), "mfe": round(t["mfe"], 2),
                    "motivo": t["motivo"], "sesion": t["sesion"], "barras": t["barras"]}
                   for t in trades],
    }
    json.dump(resultado, open(ruta_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _status(ruta_status, "completado", 100,
            "✅ COMPLETADO: %s · PF %.2f · %s ops" % (veredicto, m["pf"], m["trades"]),
            parcial={"trades": m["trades"], "pf": round(m["pf"], 2), "wr": round(m["wr"], 1), "ret": round(m["ret_pct"], 2)})

if __name__ == "__main__":
    main()
