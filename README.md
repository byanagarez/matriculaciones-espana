# Matriculaciones de turismos en España (2020-2025)

Pipeline en Python + dashboard en Power BI que convierte los **microdatos mensuales de matriculaciones de la DGT** (72 ficheros de ancho fijo, varios millones de vehículos) en una tabla limpia para analizar:

- ranking de marcas y modelos más matriculados,
- evolución mensual del mercado,
- transición hacia la electrificación (híbridos, enchufables, eléctricos),
- crecimiento de las marcas chinas.

![Dashboard](docs/dashboard.png)

## ¿Por qué he hecho este proyecto?

Es interesante ver cómo evoluciona el mercado del automóvil, sobre todo durante estos últimos años en los que se han visto tantas nuevas marcas aparecer en España; sin embargo, a pesar de que los datos de la DGT están disponibles para todo el mundo y hay informes, eché en falta otra forma de explorar los datos de forma visual, con la posibilidad de comparar marcas, modelos o ver el aumento de los vehículos híbridos y eléctricos. 

Por eso nace este proyecto de análisis propio a partir de los microdatos oficiales de la Dirección General de Tráfico (DGT), abarcando los últimos 6 años disponibles, desde 2020 a 2025.Para delimitar el mercado estudiado, se excluyen vehículos comerciales, especiales y determinados usos profesionales mediante reglas de filtrado propias.

## El reto de los datos

Los TXT de la DGT son registros de **714 caracteres** sin separadores y con texto sucio: la misma marca aparece como `VW`, `VOLKSWAGEN VW` o `VOLKSWAGEN AG`; el modelo viene cortado a 22 caracteres (`C3 AIRCR`), con el acabado pegado (`N 2008 ALLURE PURETECH`) o con la marca repetida (`CITROEN CITROEN C3`). Antes de la limpieza de los datos y normalización de las variables, siguiendo unos criterios explicados más adelante, un mismo modelo se reparte en cientos de etiquetas que hacen que los rankings no sean realistas.

## Qué hace el código

1. **Lee** los TXT línea a línea (posiciones verificadas contra el [diseño de registro oficial](https://www.dgt.es/export/sites/web-DGT/.galleries/downloads/dgt-en-cifras/matraba/MATRICULACIONES_MATRABA.pdf): 69 campos, 714 caracteres).
2. **Filtra turismos nuevos de matriculación ordinaria** (ver definición más abajo).
3. **Normaliza** marcas y modelos con catálogos editables (`catalogos/*.csv`) y reglas por serie (BMW 120d → `SERIE 1`, Mercedes A 200 → `CLASE A`).
4. **Rescata** modelos no reconocidos usando el `CODIGO_ITV` (código de homologación) de vehículos ya clasificados.
5. **Agrega** por mes, marca, modelo y tecnología y **escribe auditorías** de todo lo que descarta o no puede clasificar.

## Definición de "turismo" para la limpieza

El análisis se centra en turismos convencionales. Aunque se parte de vehículos clasificados como M1, se aplican exclusiones adicionales para retirar determinados modelos comerciales, vehículos especiales y usos profesionales. Por ello, los totales pueden diferir de las estadísticas oficiales de matriculaciones de la DGT.

Concretamente, un registro entra si cumple **todo** lo siguiente:

| Condición | Campo | Valor |
|---|---|---|
| Categoría de homologación | `CATEGORIA_HOMOLOGACION_UE` | `M1` |
| Vehículo nuevo | `IND_NUEVO_USADO` | `N` |
| Matriculación ordinaria | `CLAVE_TRAMITE` | `1` |
| No es carrocería especial (autocaravana, ambulancia, blindado...) | `CARROCERIA` | no empieza por `S` |
| Servicio de turismo | `SERVICIO` | no es ambulancia, funerario, policía, bomberos, vivienda... |
| Modelo | catálogo | no es una furgoneta grande (Transit, Sprinter, Crafter, Master, Trafic...) |

Los monovolúmenes y vehículos de pasajeros derivados de furgoneta (Proace City Verso, Berlingo, Zafira Life, Clase V) **se mantienen**: son M1.
Taxis y coches de alquiler **se mantienen**: son turismos que se matriculan como cualquier otro.

Todo lo excluido queda en `data/processed/registros_excluidos.csv` con su motivo, para que la decisión sea auditable.

## Estructura

```
├── procesar_matriculaciones.py   # lectura de TXT, filtros, agregación, informes
├── normalizacion.py              # limpieza de marcas y modelos
├── MATRICULACIONES.pbix          # dashboard interactivo en Power BI
├── README.md
├── requirements.txt
├── catalogos/
│   ├── marcas.csv                # alias, marcas a excluir, transformadores
│   ├── modelos.csv               # marca, patrón, modelo normalizado, incluir (1/0)
│   └── cambios_marca.csv         # CUPRA registrado bajo SEAT, ABARTH bajo FIAT...
├── tests/                        # pruebas con ficheros sintéticos
├── data/
│   ├── raw/                      # aquí van los TXT descargados (no se versionan)
│   └── processed/                # resultados
└── docs/
    └── dashboard.png             # captura del dashboard
```

## Cómo reproducirlo

```bash
pip install -r requirements.txt

# 1. Descarga los 72 ficheros mensuales (2020-2025) de la DGT y colócalos en data/raw/
#    https://www.dgt.es/menusecundario/dgt-en-cifras/dgt-en-cifras-resultados/dgt-en-cifras-detalle/Microdatos-de-Matriculaciones-de-Vehiculos-mensual/
#    Nombres esperados: export_mensual_mat_202001.txt ... export_mensual_mat_202512.txt

# 2. (Recomendado) Mira qué valores de carrocería, servicio, etc. hay de verdad
python procesar_matriculaciones.py --perfilar

# 3. Proceso completo
python procesar_matriculaciones.py

# Pruebas
python -m unittest discover -s tests -v
```

### Resultados (`data/processed/`)

| Archivo | Contenido |
|---|---|
| `matriculaciones_turismos.csv` | **Tabla para Power BI**: periodo, año, mes, marca, modelo, tecnología, matriculaciones |
| `modelos_pendientes_revision.csv` | Modelos que el catálogo no reconoce, ordenados por volumen: **la lista de tareas para mejorar la limpieza** |
| `auditoria_modelos.csv` | Cada modelo original, a qué se normalizó y con qué método |
| `auditoria_marcas.csv` | Cada marca original y su normalización |
| `registros_excluidos.csv` | Qué se descartó y por qué |
| `resumen_procesamiento.csv` | Contadores por fichero (líneas leídas, descartadas por cada motivo) |

# Resumen procesamiento

| Fase | Registros |
|---|---|
| Total leído | 9.426.678 |
| Excluidos por no ser M1 | 2.722.806 |
| Excluidos por no ser nuevos | 752.817 |
| Excluidos por trámite no ordinario | 2.968 |
| Candidatos a turismo | 5.948.087 |
| Excluidos por otras reglas | 91.487 |
| **Turismos finales** | **5.856.600** |

## Cómo mejorar la clasificación

No hace falta tocar el código. Abre `modelos_pendientes_revision.csv`, mira los modelos con más matriculaciones y añade una fila en `catalogos/modelos.csv`:

```
marca,patron,modelo,incluir
RENAULT,KADJAR,KADJAR,1
```

El patrón coincide si el modelo **empieza** por él (`C3` coincide con `C3 PURETECH` pero no con `C30`). Con `*` delante el texto solo debe **contener** la palabra (`*BERLINGO`). Vuelve a ejecutar y comprueba el porcentaje de modelos clasificados que se imprime al final.

## Limitaciones

- **Nivel de detalle: familia de modelo.** `A3 Sportback` y `A3` cuentan como `A3`; `C3` y `C3 Aircross` se mantienen separados porque son coches distintos.
- **`MODELO_ITV` tiene 22 caracteres.** Los nombres largos llegan cortados; el catálogo lo tiene en cuenta y el `CODIGO_ITV` rescata parte de los casos.
- **"Híbrido no enchufable" (HEV)** sigue la categoría de vehículo eléctrico registrada por la DGT y probablemente incluye microhíbridos (MHEV). La cuota de electrificación depende de esa definición.
- **"Sin etiqueta de electrificación"** agrupa gasolina, diésel, gas y todo lo que no declara categoría eléctrica.
- **El periodo es el del fichero mensual**, no la fecha exacta de matriculación; el número de registros con fecha distinta se anota en `resumen_procesamiento.csv`.
- Las autocaravanas se filtran por carrocería y servicio, así que la calidad del filtro depende de que esos campos estén bien informados (por eso existe `--perfilar`).

El proceso ha permitido normalizar el modelo del 99,94 % de las matriculaciones. Los casos que no se han podido identificar se agrupan bajo la categoría OTROS de cada marca y quedan registrados para posibles revisiones futuras.

## Licencia

Código bajo licencia MIT. 

Fuente de datos: Dirección General de Tráfico (DGT), *Microdatos de Matriculaciones de Vehículos (mensual)*, 2020–2025. Datos reutilizados bajo licencia CC BY 4.0. Los registros se han filtrado, normalizado y agregado para este proyecto. Los resultados no constituyen estadísticas oficiales de la DGT.
