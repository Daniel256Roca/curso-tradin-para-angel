# estrategia_demo.py — cruce EMA 9/21 con stop y objetivo por ATR
# Pruébala:   python3 probador_estrategias.py estrategia_demo.py --activo oro
# Las variables EN MAYÚSCULAS son parámetros: el probador los moverá ±25%
# para ver si tu estrategia está sobreoptimizada.
PERIODO_RAPIDA = 9
PERIODO_LENTA  = 21
STOP_ATR   = 2.0   # stop = 2 × ATR(14)
TAKE_ATR   = 3.0   # objetivo = 3 × ATR(14)
RIESGO_PCT = 1.0   # arriesga el 1% de la cuenta por operación

def senal(i, velas):
    if i < PERIODO_LENTA + 5:
        return 0
    cierres = [v["c"] for v in velas]
    ra, la = ema(cierres[:-1], PERIODO_RAPIDA), ema(cierres[:-1], PERIODO_LENTA)
    rb, lb = ema(cierres, PERIODO_RAPIDA),      ema(cierres, PERIODO_LENTA)
    if ra <= la and rb > lb:
        return 1     # cruce al alza → comprar
    if ra >= la and rb < lb:
        return -1    # cruce a la baja → vender
    return 0
