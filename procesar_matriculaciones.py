"""Procesa los microdatos mensuales de matriculaciones de la DGT (MATRABA).

Qué hace, paso a paso:
  1. Lee los 72 TXT mensuales (2020-2025) línea a línea. Cada línea es UN vehículo
     y todos los campos ocupan posiciones fijas (ver LAYOUT más abajo).
  2. Se queda solo con TURISMOS NUEVOS de MATRICULACIÓN ORDINARIA:
        categoría M1 + nuevo + trámite 1 + carrocería/servicio de turismo.
  3. Limpia marca y modelo (ver normalizacion.py y la carpeta catalogos/).
  4. Cuenta cuántos vehículos hay por mes, marca, modelo y tecnología.
  5. Escribe un CSV listo para Power BI y varios informes de auditoría.

Uso:
    python procesar_matriculaciones.py --perfilar     # 1º: mirar qué valores hay en los TXT
    python procesar_matriculaciones.py                # proceso completo
    python procesar_matriculaciones.py --origen D:/dgt --salida D:/resultado

Los TXT NO se suben a Git (pesan varios GB): se descargan de la DGT.
Ver README.md.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

from normalizacion import SIN_MARCA, Normalizador, limpiar_texto

RAIZ = Path(__file__).resolve().parent
ORIGEN_DEFECTO = RAIZ / "data" / "raw"
SALIDA_DEFECTO = RAIZ / "data" / "processed"
ANIOS = range(2020, 2026)          # 2020 .. 2025 (6 años, 72 ficheros)
PATRON_FICHERO = "export_mensual_mat_{anio}{mes:02d}.txt"
ENCODING = "latin-1"               # 1 byte = 1 carácter, así las posiciones fijas no se mueven

# ----------------------------------------------------------------------------
# LAYOUT: posiciones de cada campo dentro de la línea, como (inicio, fin) de un
# "slice" de Python: linea[inicio:fin]. Verificadas sumando las longitudes del
# documento oficial "MATRICULACIONES MATRABA" (DGT): 69 campos, 714 caracteres.
# https://www.dgt.es/export/sites/web-DGT/.galleries/downloads/dgt-en-cifras/matraba/MATRICULACIONES_MATRABA.pdf
# ----------------------------------------------------------------------------
LONGITUD_REGISTRO = 714
LAYOUT = {
    "FEC_MATRICULA":                (0, 8),      # DDMMAAAA
    "MARCA_ITV":                    (17, 47),
    "MODELO_ITV":                   (47, 69),    # solo 22 caracteres: los nombres largos vienen cortados
    "CLAVE_TRAMITE":                (156, 157),  # 1 = matriculación ordinaria
    "IND_NUEVO_USADO":              (178, 179),  # N = nuevo
    "CODIGO_ITV":                   (180, 189),  # identifica una variante homologada concreta
    "SERVICIO":                     (189, 192),  # uso del vehículo: B00 particular, A04 taxi, B17 vivienda...
    "CATEGORIA_HOMOLOGACION_UE":    (426, 430),  # M1 = turismo (hasta 8 plazas + conductor)
    "CARROCERIA":                   (430, 434),
    "CLASIFICACION_REGLAMENTO":     (449, 453),
    "CATEGORIA_VEHICULO_ELECTRICO": (453, 457),  # HEV / PHEV / BEV / REEV / FCEV
    "MARCA_VEHICULO_BASE":          (463, 493),  # solo si es un vehículo transformado
    "COD_TIPO":                     (91, 93),
}
(F_INI, F_FIN), (MA_INI, MA_FIN), (MO_INI, MO_FIN) = LAYOUT["FEC_MATRICULA"], LAYOUT["MARCA_ITV"], LAYOUT["MODELO_ITV"]

TECNOLOGIAS = {
    "HEV": "Híbrido no enchufable",       # incluye microhíbridos (MHEV): ver README
    "PHEV": "Híbrido enchufable",
    "BEV": "Eléctrico puro",
    "REEV": "Eléctrico autonomía extendida",
    "FCEV": "Pila de combustible",
}
SIN_ETIQUETA = "Sin etiqueta de electrificación"

# ----------------------------------------------------------------------------
# DEFINICIÓN DE "TURISMO". Todo lo que NO cumpla esto se descarta y queda
# registrado en registros_excluidos.csv, con su motivo.
# Estos valores son editables. Ejecuta --perfilar para ver qué códigos existen
# de verdad en tus ficheros antes de fiarte de ellos.
# ----------------------------------------------------------------------------
# Carrocería: según la Directiva 2007/46/CE los turismos son AA-AF (berlina, familiar,
# coupé, descapotable, monovolumen) y los códigos que empiezan por S son de uso especial
# (SA autocaravana, SB blindado, SC ambulancia, SD coche fúnebre...).
CARROCERIAS_EXCLUIDAS_PREFIJO = ("S",)
# Servicio (campo SERVICIO) que no corresponde a un turismo "normal".
SERVICIOS_EXCLUIDOS = {
    "A05": "auxilio en carretera", "A07": "ambulancia", "A08": "funerario",
    "A10": "mercancías peligrosas", "A11": "basurero", "A13": "policía",
    "A14": "bomberos", "A15": "protección civil", "A16": "defensa",
    "A20": "mercancías perecederas", "B17": "vivienda (autocaravana)",
}


def nombre_fichero(anio: int, mes: int) -> str:
    return PATRON_FICHERO.format(anio=anio, mes=mes)


def comprobar_ficheros(origen: Path, nombres: set[str]) -> None:
    existentes = {p.name for p in origen.glob("export_mensual_mat_*.txt")}
    faltan = sorted(nombres - existentes)
    if faltan:
        raise FileNotFoundError(f"Faltan {len(faltan)} ficheros en {origen}: {faltan[:5]}...")


def es_fecha_valida(texto: str) -> bool:
    try:
        date(int(texto[4:8]), int(texto[2:4]), int(texto[0:2]))
        return True
    except ValueError:
        return False


def motivo_no_turismo(carroceria: str, servicio: str) -> str:
    """Devuelve el motivo de exclusión por carrocería/servicio, o '' si es turismo."""
    if carroceria.startswith(CARROCERIAS_EXCLUIDAS_PREFIJO):
        return f"carroceria_especial:{carroceria}"
    if servicio in SERVICIOS_EXCLUIDOS:
        return f"servicio:{servicio}:{SERVICIOS_EXCLUIDOS[servicio]}"
    return ""


def leer_lineas(ruta: Path):
    """Itera las líneas del TXT. Se lee en latin-1 para no desplazar posiciones."""
    with ruta.open("r", encoding=ENCODING, errors="replace", newline="") as f:
        for linea in f:
            yield linea.rstrip("\r\n")


# ----------------------------------------------------------------------------
# MODO --perfilar: qué valores hay realmente en los campos clave
# ----------------------------------------------------------------------------
def perfilar(origen: Path, salida: Path) -> None:
    """Cuenta valores de carrocería, servicio y clasificación en turismos nuevos.

    Solo lee junio de cada año, para que tarde segundos y no minutos.
    """
    salida.mkdir(parents=True, exist_ok=True)
    campos = ["CARROCERIA", "SERVICIO", "CLASIFICACION_REGLAMENTO", "COD_TIPO", "CATEGORIA_VEHICULO_ELECTRICO"]
    cuentas = {c: Counter() for c in campos}
    ficheros = [origen / nombre_fichero(a, 6) for a in ANIOS]
    for ruta in ficheros:
        if not ruta.exists():
            print(f"Aviso: no existe {ruta.name}, se omite")
            continue
        for linea in leer_lineas(ruta):
            if len(linea) != LONGITUD_REGISTRO or linea[426:428] != "M1" or linea[178:179] != "N" or linea[156:157] != "1":
                continue
            for c in campos:
                i, j = LAYOUT[c]
                cuentas[c][linea[i:j].strip()] += 1
    filas = [(c, valor, n) for c in campos for valor, n in cuentas[c].most_common()]
    destino = salida / "perfil_campos.csv"
    pd.DataFrame(filas, columns=["campo", "valor", "registros"]).to_csv(destino, index=False, encoding="utf-8-sig")
    for c in campos:
        print(f"\n{c}: {dict(cuentas[c].most_common(12))}")
    print(f"\nPerfil completo en {destino}")


# ----------------------------------------------------------------------------
# PROCESO COMPLETO
# ----------------------------------------------------------------------------
def procesar(origen: Path, salida: Path, incluir_no_turismos: bool = False) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    comprobar_ficheros(origen, {nombre_fichero(a, m) for a in ANIOS for m in range(1, 13)})
    norm = Normalizador()

    # Las mismas marcas/modelos se repiten millones de veces: guardamos en memoria el
    # resultado de cada combinación para no recalcularlo (acelera el proceso muchísimo).
    @lru_cache(maxsize=None)
    def clasificar(marca_txt: str, modelo_txt: str, marca_base_txt: str):
        return (norm.clasificar(marca_txt, modelo_txt, marca_base_txt),
                limpiar_texto(marca_txt), limpiar_texto(modelo_txt))

    # (periodo, anio, mes, marca, modelo, tecnologia) -> nº de vehículos
    grupos: Counter = Counter()
    # Registros que no pudimos clasificar con el catálogo. Guardamos el CODIGO_ITV para
    # intentar rescatarlos después (ver _rescatar_por_codigo_itv).
    pendientes: Counter = Counter()
    votos_itv: dict = defaultdict(Counter)       # (marca, codigo_itv) -> Counter(modelo)
    excluidos: Counter = Counter()
    auditoria_marcas: Counter = Counter()
    auditoria_modelos: Counter = Counter()
    codigos_sin_mapear: Counter = Counter()
    resumen = []

    for anio in ANIOS:
        for mes in range(1, 13):
            ruta = origen / nombre_fichero(anio, mes)
            c: Counter = Counter()
            periodo = f"{anio}-{mes:02d}-01"
            for linea in leer_lineas(ruta):
                if not linea or linea.startswith("Veh"):
                    continue                                   # línea vacía o cabecera ("Vehículos matriculados...")
                c["lineas_leidas"] += 1
                if len(linea) != LONGITUD_REGISTRO:
                    c["longitud_invalida"] += 1
                    continue
                # Filtros baratos primero (comparan 1-2 caracteres): descartan >90 % de líneas.
                if linea[426:428] != "M1":
                    c["no_M1"] += 1
                    continue
                if linea[178:179] != "N":
                    c["no_nuevo"] += 1
                    continue
                if linea[156:157] != "1":
                    c["no_ordinario"] += 1
                    continue
                if not es_fecha_valida(linea[F_INI:F_FIN]):
                    c["fecha_invalida"] += 1
                    continue
                if linea[F_INI + 4:F_FIN] != str(anio) or linea[F_INI + 2:F_INI + 4] != f"{mes:02d}":
                    # No se descarta: el periodo es el del fichero. Solo se anota para auditar.
                    c["fecha_distinta_al_fichero"] += 1
                c["candidatos_turismo"] += 1

                marca_txt = linea[MA_INI:MA_FIN]
                modelo_txt = linea[MO_INI:MO_FIN]
                marca_base_txt = linea[463:493]
                carroceria = linea[430:434].strip()
                servicio = linea[189:192].strip()
                codigo_itv = linea[180:189].strip()
                codigo_tec = linea[453:457].strip().upper()
                tecnologia = TECNOLOGIAS.get(codigo_tec, SIN_ETIQUETA)
                if codigo_tec and codigo_tec not in TECNOLOGIAS:
                    codigos_sin_mapear[codigo_tec] += 1

                cl, marca_limpia, modelo_limpio = clasificar(marca_txt, modelo_txt, marca_base_txt)
                auditoria_marcas[(marca_limpia, cl.marca)] += 1

                motivo = motivo_no_turismo(carroceria, servicio) or cl.motivo_exclusion
                if not cl.incluir:
                    motivo = motivo or "modelo_no_turismo"
                if motivo and not incluir_no_turismos:
                    excluidos[(anio, mes, motivo, marca_limpia, cl.marca, modelo_limpio)] += 1
                    c["excluidos_no_turismo"] += 1
                    continue

                if cl.metodo == "sin_clasificar":
                    pendientes[(periodo, anio, mes, cl.marca, modelo_limpio, tecnologia, codigo_itv)] += 1
                else:
                    grupos[(periodo, anio, mes, cl.marca, cl.modelo, tecnologia)] += 1
                    auditoria_modelos[(cl.marca, modelo_limpio, cl.modelo, cl.metodo)] += 1
                    if codigo_itv and cl.metodo != "sin_modelo":
                        votos_itv[(cl.marca, codigo_itv)][cl.modelo] += 1
                c["incluidos"] += 1
            resumen.append({"archivo": ruta.name, "anio": anio, "mes": mes, **c})
            print(f"{anio}-{mes:02d}: {c['incluidos']:>7,} turismos | {c['excluidos_no_turismo']:>5,} excluidos | "
                  f"{c['candidatos_turismo']:>7,} candidatos")

    _rescatar_por_codigo_itv(pendientes, votos_itv, grupos, auditoria_modelos)
    _escribir_resultados(salida, grupos, auditoria_modelos, auditoria_marcas, excluidos, resumen, codigos_sin_mapear)


def _rescatar_por_codigo_itv(pendientes, votos_itv, grupos, auditoria_modelos, umbral=0.8, minimo=3):
    """Segunda pasada: clasifica por CODIGO_ITV lo que el catálogo no reconoció.

    El CODIGO_ITV identifica una variante homologada concreta, así que casi siempre
    pertenece a un único modelo. Si el 80 % o más de los vehículos YA clasificados con
    ese código (de esa marca) son, por ejemplo, "KADJAR", asignamos "KADJAR" a los que
    quedaron sin clasificar y tienen ese mismo código. Lo que no se pueda rescatar queda
    como "OTROS <MARCA>".
    """
    for (periodo, anio, mes, marca, modelo_orig, tecnologia, codigo), n in pendientes.items():
        modelo, metodo = f"OTROS {marca}", "sin_clasificar"
        votos = votos_itv.get((marca, codigo))
        if votos and marca != SIN_MARCA:
            top, cuantos = votos.most_common(1)[0]
            total = sum(votos.values())
            if total >= minimo and cuantos / total >= umbral:
                modelo, metodo = top, "codigo_itv"
        grupos[(periodo, anio, mes, marca, modelo, tecnologia)] += n
        auditoria_modelos[(marca, modelo_orig, modelo, metodo)] += n


def _escribir_resultados(salida, grupos, auditoria_modelos, auditoria_marcas, excluidos, resumen, codigos_sin_mapear):
    if not grupos:
        raise ValueError("No se obtuvo ningún registro: revisa el LAYOUT y la carpeta de origen.")
    tabla = pd.DataFrame([(*k, n) for k, n in grupos.items()],
                         columns=["periodo", "anio", "mes", "marca", "modelo_normalizado", "tecnologia", "matriculaciones"])
    tabla = tabla.sort_values(["anio", "mes", "marca", "modelo_normalizado", "tecnologia"])
    tabla.to_csv(salida / "matriculaciones_turismos.csv", index=False, encoding="utf-8-sig")

    aud = pd.DataFrame([(*k, n) for k, n in auditoria_modelos.items()],
                       columns=["marca", "modelo_original", "modelo_normalizado", "metodo", "matriculaciones"])
    aud = aud.groupby(["marca", "modelo_original", "modelo_normalizado", "metodo"], as_index=False).matriculaciones.sum()
    aud = aud.sort_values("matriculaciones", ascending=False)
    aud.to_csv(salida / "auditoria_modelos.csv", index=False, encoding="utf-8-sig")
    # El fichero que hay que mirar para seguir mejorando la limpieza:
    aud[aud.metodo == "sin_clasificar"].to_csv(salida / "modelos_pendientes_revision.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame([(*k, n) for k, n in auditoria_marcas.items()],
                 columns=["marca_original", "marca_normalizada", "matriculaciones"]) \
        .sort_values("matriculaciones", ascending=False).to_csv(salida / "auditoria_marcas.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([(*k, n) for k, n in excluidos.items()],
                 columns=["anio", "mes", "motivo", "marca_original", "marca_normalizada", "modelo_original", "registros"]) \
        .to_csv(salida / "registros_excluidos.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(resumen).fillna(0).to_csv(salida / "resumen_procesamiento.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"codigo": k, "registros": v} for k, v in codigos_sin_mapear.most_common()],
                 columns=["codigo", "registros"]).to_csv(salida / "codigos_tecnologia_no_mapeados.csv", index=False, encoding="utf-8-sig")

    total = int(tabla.matriculaciones.sum())
    otros = int(tabla[tabla.modelo_normalizado.str.startswith("OTROS ")].matriculaciones.sum())
    print(f"\nTOTAL TURISMOS: {total:,} | EXCLUIDOS: {sum(excluidos.values()):,}")
    print(f"Modelos clasificados: {1 - otros / total:.2%} (sin clasificar: {otros:,})")
    print("Por año:", tabla.groupby("anio").matriculaciones.sum().to_dict())
    print(f"Resultados en: {salida}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--origen", type=Path, default=ORIGEN_DEFECTO, help="carpeta con los TXT (por defecto data/raw)")
    p.add_argument("--salida", type=Path, default=SALIDA_DEFECTO, help="carpeta de resultados (por defecto data/processed)")
    p.add_argument("--perfilar", action="store_true", help="solo muestra qué valores hay en carrocería, servicio...")
    p.add_argument("--incluir-no-turismos", action="store_true",
                   help="no descarta autocaravanas, ambulancias, furgonetas...; solo las anota")
    args = p.parse_args(argv)
    if args.perfilar:
        perfilar(args.origen, args.salida)
    else:
        procesar(args.origen, args.salida, args.incluir_no_turismos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
