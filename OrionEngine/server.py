#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORION ENGINE · server.py  (cero dependencias: solo Python 3 estándar)
=====================================================================
  python server.py            →  http://localhost:8787

API:
  GET  /                        → dashboard (SPA)
  POST /api/dataset             → sube CSV (body crudo) + ?nombre=  → validación estricta
  GET  /api/datasets            → lista datasets
  POST /api/estrategia/validar  → valida código .py (body crudo) sin correr backtest
  POST /api/run                 → JSON {dataset_id, codigo, spread} → lanza backtest aislado
  GET  /api/run/<id>/status     → fase, %, log, métricas parciales
  GET  /api/run/<id>            → resultado completo
  GET  /api/runs                → historial de runs (SQLite)
"""
import csv, io, json, math, os, re, sqlite3, statistics, subprocess, sys, threading, time, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
DIR_DS = os.path.join(DATA, "datasets")
DIR_RUN = os.path.join(DATA, "runs")
DB = os.path.join(DATA, "orion.db")
for d in (DATA, DIR_DS, DIR_RUN):
    os.makedirs(d, exist_ok=True)
MAX_BARRAS = 300_000
TIMEOUT_WORKER = 240  # s

# ============================== DB (SQLite, stdlib) =========================
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS datasets(
        id TEXT PRIMARY KEY, nombre TEXT, filas INT, tf TEXT, ini TEXT, fin TEXT,
        issues TEXT, creado TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS runs(
        id TEXT PRIMARY KEY, dataset_id TEXT, nombre_est TEXT, codigo TEXT,
        spread REAL, creado TEXT, veredicto TEXT, clase TEXT, pf REAL, trades INT,
        resultado TEXT)""")
    return con

# ============================== VALIDADOR ESTRICTO DE CSV ===================
def _sniff(txt):
    cab = txt[:4096]
    primera = cab.splitlines()[0] if cab.splitlines() else ""
    for d in ("\t", ";", ",", "|"):
        if primera.count(d) >= 3: return d
    return ","

def _parse_float(s):
    """Inteligente: 1.08500 · 1,08500 (coma decimal) · 1.234,56 (UE) · 1,234.56 (US)."""
    s = s.strip().replace(" ", "").replace("\u00a0", "")
    if "," in s:
        if "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")   # 1.234,56 (europeo)
            else:
                s = s.replace(",", "")                      # 1,234.56 (US miles)
        else:
            s = s.replace(",", ".")                         # 1,08500 (decimal con coma)
    return float(s)

def validar_csv(txt):
    """Devuelve (velas_normalizadas, issues, meta) o lanza ValueError con lista de errores claros."""
    delim = _sniff(txt)
    filas = list(csv.reader(io.StringIO(txt), delimiter=delim))
    if len(filas) < 3:
        raise ValueError(json.dumps([{"n": 1, "linea": 1, "causa": "archivo",
            "detalle": "Menos de 3 líneas: no parece un CSV de velas."}], ensure_ascii=False))
    errores, warnings = [], []
    # cabecera
    cab = [c.strip().lower().strip("<>").replace('"', "") for c in filas[0]]
    es_cab = any(w in ("date", "time", "open", "high", "low", "close", "fecha", "hora",
                       "apertura", "max", "min", "cierre", "vol", "tickvol", "spread") for w in cab)
    def col(*nombres):
        for n in nombres:
            if n in cab: return cab.index(n)
        return None
    i_f, i_t = col("date", "fecha", "day"), col("time", "hora")
    i_o, i_h = col("open", "apertura", "o"), col("high", "max", "alto", "h")
    i_l, i_c = col("low", "min", "bajo", "l"), col("close", "cierre", "last", "c")
    inicio = 1 if es_cab else 0
    if not es_cab:
        # formato MT5 sin cabecera: date time open high low close [tickvol vol spread]
        i_f, i_t, i_o, i_h, i_l, i_c = 0, 1, 2, 3, 4, 5
        warnings.append("Sin cabecera detectada: asumo formato MT5  <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> …")
    if i_c is None:
        errores.append({"n": 1, "linea": 1, "causa": "columnas",
            "detalle": "No encuentro columna CLOSE/cierre. Cabeceras vistas: %s" % ", ".join(cab[:10])})
    if errores:
        raise ValueError(json.dumps(errores, ensure_ascii=False))
    velas, vistos, malas = [], {}, 0
    for ln, fila in enumerate(filas[inicio:], inicio + 1):
        if not fila or all(not x.strip() for x in fila): continue
        try:
            fecha = fila[i_f].strip() if i_f is not None else ""
            if i_t is not None and fecha and len(fila) > i_t:
                t = fila[i_t].strip()
                if t and re.match(r"^\d{1,2}:\d{2}", t): fecha = f"{fecha} {t}"
            def num(ix):
                return _parse_float(fila[ix]) if ix is not None and ix < len(fila) and fila[ix].strip() != "" else None
            o, h, l, c = num(i_o), num(i_h), num(i_l), num(i_c)
            if c is None or c <= 0: raise ValueError("cierre inválido")
            o = c if o is None else o; h = c if h is None else h; l = c if l is None else l
            if l > min(o, c) + 1e-9 or h < max(o, c) - 1e-9:
                warnings.append("Línea %d: high/low incoherentes (se acepta igualmente)." % ln)
            if fecha in vistos:
                errores.append({"n": len(errores)+1, "linea": ln, "causa": "duplicado",
                                "detalle": "Vela duplicada en «%s» (ya vista en línea %d). Limpia el export." % (fecha, vistos[fecha])})
                continue
            vistos[fecha] = ln
            velas.append({"fecha": fecha, "o": o, "h": h, "l": l, "c": c})
        except (ValueError, IndexError):
            malas += 1
    if malas:
        warnings.append("%d líneas no parseables ignoradas (formato distinto al resto)." % malas)
    # orden temporal + huecos
    def ts(f):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S",
                    "%d/%m/%Y %H:%M", "%Y-%m-%d", "%Y.%m.%d", "%d/%m/%Y",
                    "%Y-%m-%d %H:%M:%S.%f"):
            try: return datetime.strptime(f, fmt)
            except ValueError: pass
        return None
    tss = [ts(v["fecha"]) for v in velas]
    if all(t is not None for t in tss):
        inver = sum(1 for a, b in zip(tss, tss[1:]) if b < a)
        if inver:
            errores.append({"n": len(errores)+1, "linea": "-", "causa": "desorden temporal",
                            "detalle": "%d velas fuera de orden. Ordena el export por fecha ascendente." % inver})
        deltas = [b - a for a, b in zip(tss, tss[1:]) if b > a]
        if deltas:
            med = sorted(deltas)[len(deltas)//2]
            if med.total_seconds() > 0:
                huecos = [(a, b) for a, b in zip(tss, tss[1:])
                          if a and b and b > a
                          and (b - a).total_seconds() > med.total_seconds() * 2.5
                          and not (a.weekday() == 4 and b.weekday() == 0)]  # fin de semana ≠ hueco
                ratio = len(huecos) / len(deltas)
                if ratio > 0.02:
                    big = max(huecos, key=lambda p: (p[1] - p[0]).total_seconds())
                    errores.append({"n": len(errores)+1, "linea": "-", "causa": "huecos de datos",
                        "detalle": "%d huecos (%.1f%% del histórico; el mayor %s → %s). Un backtest con agujeros miente."
                        % (len(huecos), ratio*100, big[0], big[1])})
                elif huecos:
                    big = max(huecos, key=lambda p: (p[1] - p[0]).total_seconds())
                    warnings.append("%d hueco(s) puntual(es) — el mayor: %s → %s. Si son festivos/cierres, aceptable."
                                    % (len(huecos), big[0], big[1]))
                tf = med
        else:
            tf = None
    else:
        tf = None
        warnings.append("No pude interpretar todas las fechas: compruebo duplicados pero no huecos ni orden.")
    if len(velas) < 150:
        errores.append({"n": len(errores)+1, "linea": "-", "causa": "histórico corto",
                        "detalle": "Solo %d velas válidas: mínimo 150 para un backtest con calentamiento." % len(velas)})
    if len(velas) > MAX_BARRAS:
        errores.append({"n": len(errores)+1, "linea": "-", "causa": "demasiado grande",
                        "detalle": "%d velas > límite %d. Recorta el rango (consejo: 1-3 años de M15/H1 rinde mejor que 10 de M1)." % (len(velas), MAX_BARRAS)})
    if errores:
        raise ValueError(json.dumps(errores, ensure_ascii=False))
    tf_txt = ""
    try:
        sec = tf.total_seconds() if tf else 0
        tf_txt = {60: "M1", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1"}.get(int(sec), "%d s" % sec)
    except Exception:
        pass
    meta = {"tf": tf_txt or "n/d", "delimitador": repr(delim)}
    return velas, warnings, meta

def guardar_dataset(nombre, velas, warnings, meta):
    did = uuid.uuid4().hex[:10]
    ruta = os.path.join(DIR_DS, did + ".csv")
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["fecha", "open", "high", "low", "close"])
        for v in velas: w.writerow([v["fecha"], v["o"], v["h"], v["l"], v["c"]])
    con = db()
    con.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?)",
                (did, nombre, len(velas), meta.get("tf", "n/d"), velas[0]["fecha"], velas[-1]["fecha"],
                 json.dumps(warnings, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close()
    return did

# ============================== RUNS (proceso aislado) ======================
def lanzar_run(dataset_id, codigo, spread):
    con = db()
    fila = con.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if not fila:
        con.close(); return None, "dataset no encontrado"
    rid = uuid.uuid4().hex[:10]
    rdir = os.path.join(DIR_RUN, rid); os.makedirs(rdir, exist_ok=True)
    ruta_est = os.path.join(rdir, "estrategia.py")
    open(ruta_est, "w", encoding="utf-8").write(codigo)
    ruta_ds = os.path.join(DIR_DS, dataset_id + ".csv")
    ruta_out = os.path.join(rdir, "resultado.json")
    ruta_st = os.path.join(rdir, "status.json")
    con.execute("INSERT INTO runs(id, dataset_id, nombre_est, codigo, spread, creado, veredicto, clase, pf, trades, resultado) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, dataset_id, "estrategia.py", codigo, spread, datetime.now().isoformat(timespec="seconds"),
                 "ejecutando…", "run", None, None, ""))
    con.commit(); con.close()

    def _proc():
        py = sys.executable or "python"
        try:
            proc = subprocess.Popen([py, os.path.join(BASE, "worker.py"), ruta_ds, ruta_est, ruta_out, ruta_st, str(spread)],
                                    cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.wait(timeout=TIMEOUT_WORKER)
        except subprocess.TimeoutExpired:
            proc.kill()
            json.dump({"ok": False, "errores": [{"n": 1, "linea": "-", "causa": "timeout",
                "detalle": "El backtest superó %d s: bucle infinito o estrategia demasiado pesada por barra." % TIMEOUT_WORKER}]},
                open(ruta_out, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as e:
            json.dump({"ok": False, "errores": [{"n": 1, "linea": "-", "causa": "error interno", "detalle": str(e)}]},
                      open(ruta_out, "w", encoding="utf-8"), ensure_ascii=False)
        try:
            res = json.load(open(ruta_out, encoding="utf-8"))
            con = db()
            if res.get("ok"):
                con.execute("UPDATE runs SET veredicto=?, clase=?, pf=?, trades=?, resultado=? WHERE id=?",
                            (res["veredicto"]["texto"], res["veredicto"]["cls"], res["m"]["pf"], res["m"]["trades"],
                             json.dumps(res, ensure_ascii=False), rid))
            else:
                con.execute("UPDATE runs SET veredicto=?, clase=?, resultado=? WHERE id=?",
                            ("RECHAZADA", "bad", json.dumps(res, ensure_ascii=False), rid))
            con.commit(); con.close()
        except Exception:
            pass
    threading.Thread(target=_proc, daemon=True).start()
    return rid, None

# ============================== HTTP ========================================
class Orion(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))
    def _body(self):
        ln = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(ln, 60_000_000)) if ln else b""
        return raw
    def log_message(self, *a):  # silencio en consola
        pass

    def do_GET(self):
        u = urlparse(self.path); path = u.path
        if path in ("/", "/index.html"):
            self._send(200, open(os.path.join(BASE, "dashboard.html"), "rb").read(), "text/html"); return
        if path == "/api/datasets":
            con = db()
            filas = [dict(r) for r in con.execute("SELECT * FROM datasets ORDER BY creado DESC").fetchall()]
            con.close(); self._json(filas); return
        if path == "/api/runs":
            con = db()
            filas = [dict(r) for r in con.execute(
                "SELECT id, dataset_id, creado, veredicto, clase, pf, trades FROM runs ORDER BY creado DESC LIMIT 30").fetchall()]
            con.close(); self._json(filas); return
        m = re.match(r"^/api/run/([0-9a-f]{10})(/status)?$", path)
        if m:
            rid, st = m.group(1), m.group(2)
            rdir = os.path.join(DIR_RUN, rid)
            if not os.path.isdir(rdir): self._json({"error": "run no encontrado"}, 404); return
            if st:
                try: self._json(json.load(open(os.path.join(rdir, "status.json"), encoding="utf-8")))
                except Exception: self._json({"fase": "iniciando…", "pct": 0, "log": []})
                return
            try:
                self._json(json.load(open(os.path.join(rdir, "resultado.json"), encoding="utf-8")))
            except Exception:
                self._json({"ok": None, "estado": "aún corriendo"}); return
        self._json({"error": "404"}, 404)

    def do_POST(self):
        u = urlparse(self.path); path = u.path; qs = parse_qs(u.query)
        if path == "/api/dataset":
            txt = self._body().decode("utf-8-sig", "replace")
            if len(txt) < 200:
                self._json({"ok": False, "errores": [{"n": 1, "linea": "-", "causa": "archivo", "detalle": "CSV vacío o demasiado corto."}]}, 400); return
            try:
                velas, warnings, meta = validar_csv(txt)
            except ValueError as e:
                try: errores = json.loads(str(e))
                except Exception: errores = [{"n": 1, "linea": "-", "causa": "parseo", "detalle": str(e)}]
                self._json({"ok": False, "errores": errores}, 400); return
            nombre = (qs.get("nombre") or ["dataset.csv"])[0]
            did = guardar_dataset(nombre, velas, warnings, meta)
            self._json({"ok": True, "id": did, "filas": len(velas), "tf": meta.get("tf"),
                        "ini": velas[0]["fecha"], "fin": velas[-1]["fecha"], "warnings": warnings}); return
        if path == "/api/estrategia/validar":
            codigo = self._body().decode("utf-8", "replace")
            errores = []
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
                errores.append({"n": len(errores)+1, "linea": e.lineno or "?", "causa": "sintaxis Python", "detalle": e.msg})
            if not errores and ("def on_bar" not in codigo and "def senal" not in codigo):
                errores.append({"n": len(errores)+1, "linea": "-", "causa": "interfaz ausente",
                                "detalle": "Falta  def on_bar(i, velas):  (o senal(i, velas))."})
            self._json({"ok": not errores, "errores": errores}); return
        if path == "/api/run":
            try:
                payload = json.loads(self._body().decode("utf-8", "replace"))
            except Exception:
                self._json({"ok": False, "error": "JSON inválido"}, 400); return
            rid, err = lanzar_run(payload.get("dataset_id", ""), payload.get("codigo", ""), float(payload.get("spread") or 0))
            if err: self._json({"ok": False, "error": err}, 400); return
            self._json({"ok": True, "run_id": rid}); return
        self._json({"error": "404"}, 404)

if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print("=" * 58)
    print("  ORION ENGINE · plataforma de backtesting")
    print("  Abre en tu navegador:  http://localhost:%d" % puerto)
    print("  (Ctrl+C para apagar)")
    print("=" * 58)
    ThreadingHTTPServer(("0.0.0.0", puerto), Orion).serve_forever()
