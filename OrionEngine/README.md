# ◈ ORION ENGINE — Plataforma de backtesting para estrategias algorítmicas

Web app **local, monousuario, sin login y sin dependencias** (solo Python 3 estándar):
backtests **reales** bar-a-bar de estrategias Python sobre tus CSVs OHLCV,
con dashboard analítico, validador estricto y suite anti-overfitting.

## ▶ Arranque (Windows)

```
iniciar_orion.bat        →  se abre http://localhost:8787
```
(o en Linux/Mac: `./iniciar_orion.sh`)

No requiere `pip install` de nada. La base de datos es SQLite (archivo `data/orion.db`),
los datasets y runs viven en `data/`. Para migrar a MongoDB más adelante solo hay que
cambiar `db()` y las 4 consultas de `server.py`.

## 📥 Dataset

Sube el **CSV de velas** que exporta MT5 (`Archivo → Guardar` en el gráfico, o el export del Strategy Tester):

- Formatos aceptados: `<DATE>;<TIME>;<OPEN>;<HIGH>;<LOW>;<CLOSE>…` (con o sin cabecera),
  `date,open,high,low,close`, variantes en español, delimitador `;` `,` `TAB`,
  decimales con punto o con coma europea (`1.234,56`).
- Validador estricto (rechaza con **nº de error + línea + causa**): columnas ausentes,
  velas duplicadas, desorden temporal, >5% de huecos, <150 velas, >300k velas.
- Timeframe libre (M5/H1/D1… se detecta solo). Recomendado: 1-3 años de M15/H1;
  años de M5 funcionan pero la simulación será lenta si tu estrategia recorre todo el pasado cada barra.

## 🐍 Estrategia

```python
PERIODO = 14          # ← los params en MAYÚSCULAS se testean ±25% (anti-overfit)
STOP_ATR = 2.0        # o STOP_PIPS = 30 · TAKE_ATR/TAKE_PIPS · RIESGO_PCT

def on_bar(i, velas):          # velas = SOLO el pasado (0..i): el futuro está bloqueado
    if rsi([v["c"] for v in velas]) < 30: return 1
    if rsi([v["c"] for v in velas]) > 70: return -1
    return 0
```

- Anti-lookahead **por diseño**: `velas[i+1]` lanza error y la estrategia es RECHAZADA.
- Sandbox: ejecución en subproceso aislado con timeout; `import os/sys/subprocess/red/disco` → rechazo con línea exacta.
- Funciones gratis: `ema sma rsi atr maximo minimo`.
- Ejecución: entrada en apertura de la vela siguiente, SL primero (pesimista), spread configurable.

## 📊 Lo que obtienes (todo real, nada sintético)

Win Rate · Profit Factor · Expectancy · Sharpe · Sortino · Max DD (+ duración) ·
**MAE/MFE reales por operación** (scatter) · desglose por dirección y sesión ·
equity curve · tabla de operaciones con filtros · **IS/OOS 70/30 siempre lado a lado**
con alerta de sobreajuste · **sensibilidad ±25% re-corriendo el backtest por parámetro** ·
avisos de muestra pequeña y nº efectivo de parámetros · historial de runs comparable.

## 🗺️ Fases

| Fase | Estado |
|---|---|
| 1 Motor (ingesta+validador, motor bar-a-bar, sandbox, errores numerados) | ✅ |
| 2 Flujo completo (uploads, corrida con terminal en vivo + fases + métricas parciales, dashboard real) | ✅ |
| 3 Optimización (sliders → re-runs, grid + heatmap) | ✅ parcial (sensibilidad ±25% automática) · grid dedicado pendiente |
| 4 Anti-overfitting (IS/OOS siempre visible · avisos) | ✅ parcial · walk-forward y Monte Carlo pendientes |
| 5 MQ5 (automatizar Strategy Tester de MT5 en Windows) | pendiente |
| 6 Pulido visual (entrada escalonada, terminal vivo) | ✅ |

## Preguntas abiertas (respuestas por defecto)

- **Timeframe**: libre desde el inicio (el validador lo detecta); multi-timeframe real queda para fase 3+.
- **Historial**: soporta hasta 300k barras; con AÑOS de M5 las estrategias que recorren todo el pasado cada
  barra irán lentas — el consejo del validador es 1-3 años de M15/H1, que es lo óptimo ruido/velocidad.
