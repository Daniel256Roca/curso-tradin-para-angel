# estrategia_demo.py — cruce EMA 9/21 con stop y objetivo por ATR
# Pruébala:   python3 probador_estrategias.py estrategia_demo.py --activo oro
STOP_ATR   = 2.0   # stop = 2 × ATR(14)
TAKE_ATR   = 3.0   # objetivo = 3 × ATR(14)
RIESGO_PCT = 1.0   # arriesga el 1% de la cuenta por operación

def senal(i, velas):
    if i < 30:
        return 0
    cierres = [v["c"] for v in velas]
    e9a, e21a = ema(cierres[:-1], 9), ema(cierres[:-1], 21)
    e9b, e21b = ema(cierres, 9),      ema(cierres, 21)
    if e9a <= e21a and e9b > e21b:
        return 1     # cruce al alza → comprar
    if e9a >= e21a and e9b < e21b:
        return -1    # cruce a la baja → vender
    return 0
