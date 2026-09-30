# ═══════════════════════════════════════════════════════════════════
# ORION ENGINE · estrategia_ejemplo.py — cruce EMA con salidas por ATR
# ═══════════════════════════════════════════════════════════════════
# INTERFAZ (obligatoria una de las dos):
#     def on_bar(i, velas) → 1 largo · -1 corto · 0 nada      ← nueva
#     def senal(i, velas)  → igual                            ← compatible
#
# ANTI-LOOKAHEAD POR DISEÑO: `velas` solo deja leer barras 0..i.
# Si pides velas[i+1] (el futuro), Orion RECHAZA tu estrategia con el
# error "LOOKAHEAD BLOQUEADO" y la línea exacta. Ni con trampa ni sin ella.
#
# GRATIS (úsalas directo): ema() sma() rsi() atr() maximo() minimo()
# PARÁMETROS en MAYÚSCULAS: Orion los mueve ±25% y re-corre el backtest
# para decirte si están sobreoptimizados (frágil / sensible / estable).

PERIODO_RAPIDA = 9      # EMA rápida
PERIODO_LENTA  = 21     # EMA lenta
STOP_ATR   = 2.0        # stop = 2 × ATR(14)   — alternativa: STOP_PIPS = 30
TAKE_ATR   = 3.0        # objetivo = 3 × ATR   — alternativa: TAKE_PIPS = 60
RIESGO_PCT = 1.0        # % de la cuenta arriesgado por operación

def on_bar(i, velas):
    if i < PERIODO_LENTA + 5:
        return 0
    cierres = [v["c"] for v in velas]          # solo barras pasadas (la Ventana lo garantiza)
    ra, la = ema(cierres[:-1], PERIODO_RAPIDA), ema(cierres[:-1], PERIODO_LENTA)
    rb, lb = ema(cierres, PERIODO_RAPIDA),      ema(cierres, PERIODO_LENTA)
    if ra <= la and rb > lb:   return 1        # cruce al alza  → largo
    if ra >= la and rb < lb:   return -1       # cruce a la baja → corto
    return 0
