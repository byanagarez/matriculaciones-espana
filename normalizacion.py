"""Normalización de marcas y modelos de la DGT.

Este módulo NO lee los ficheros de la DGT: solo sabe convertir textos sucios
("VW", "CITROEN CITROEN C3 AIRCR", "N 2008 ALLURE PURETECH") en nombres
limpios ("VOLKSWAGEN", "C3 AIRCROSS", "2008").

Las listas de marcas y modelos NO están dentro del código sino en la carpeta
`catalogos/` (archivos CSV que se pueden abrir y editar con Excel). Así,
mejorar la clasificación es añadir filas a un CSV, no programar.

Cómo se decide el modelo, en este orden:
  1. Se limpia el texto (mayúsculas, sin tildes, sin espacios dobles).
  2. Se prueban varias versiones del texto: tal cual, sin el nombre de la
     marca delante, sin "NUEVO", sin el "N" de Peugeot, sin "E-" de eléctrico...
  3. Para cada versión se aplican primero las *reglas por serie*
     (BMW 120d -> SERIE 1, Mercedes A 200 -> CLASE A...) y después el
     *catálogo* de modelos (catalogos/modelos.csv).
  4. Si nada coincide, el modelo queda como "OTROS <MARCA>" y la fila se anota
     en `modelos_pendientes_revision.csv` para añadir una regla más adelante.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CARPETA_CATALOGOS = Path(__file__).resolve().parent / "catalogos"

SIN_MARCA = "MARCA NO INFORMADA"
SIN_MODELO = "MODELO NO INFORMADO"
VALORES_VACIOS = {"", "-", "--", "N/D", "NO CONSTA", "SIN MARCA", "SIN MODELO", "NAN"}

# Caracteres que pueden seguir al nombre de un modelo sin pertenecer a él.
# "C3" debe coincidir con "C3 PURETECH" pero NO con "C30"; por eso exigimos
# que después del patrón venga el final del texto o uno de estos separadores.
SEPARADORES = " ,(/+-.:!"

# El campo MODELO_ITV mide 22 caracteres: los nombres más largos vienen
# cortados ("C3 AIRCR"). Si el texto original ocupa los 22, permitimos que
# coincida con el comienzo de un patrón del catálogo.
LONGITUD_CAMPO_MODELO = 22

# Palabras comerciales que se pueden quitar del principio del modelo.
RUIDO_INICIAL = {"NUEVO", "NUEVA", "NEW", "SUV"}

# Otras formas en que la DGT escribe el nombre de la marca dentro del modelo.
PREFIJOS_MARCA_EXTRA = {
    "MERCEDES-BENZ": {"MERCEDES", "MERCEDES BENZ", "MERCEDES-AMG", "AMG"},
    "VOLKSWAGEN": {"VW"},
    "LYNK&CO": {"LYNK & CO", "LYNK CO"},
    "LAND ROVER": {"LAND-ROVER"},
    "ALFA ROMEO": {"ALFA"},
}

# Si el texto del modelo contiene alguna de estas palabras no es un turismo
# (ambulancias, coches fúnebres, autocaravanas, vehículos carrozados).
PALABRAS_NO_TURISMO = ("AMBULANC", "FUNERARI", "AUTOCARAVAN", "CARROZAD", "MARCO POLO", "M. P. ")

# Marcas donde "E-" delante del modelo solo indica la versión eléctrica
# (E-208 es un 208, E-C3 es un C3).
MARCAS_CON_PREFIJO_E = {"PEUGEOT", "CITROEN", "OPEL", "DS", "FIAT", "VOLKSWAGEN"}

# Reglas por serie: para marcas que numeran sus versiones (BMW 120d, 320i...)
# un catálogo de todas las combinaciones sería interminable. Cada regla es
# (expresión regular, plantilla); {0} es el primer grupo capturado.
REGLAS_SERIE: dict[str, list[tuple[re.Pattern, str]]] = {
    "BMW": [
        (re.compile(r"^M?([1-8])\d{2}(?!\d)"), "SERIE {0}"),   # 120, 118I, M135I, 225XE
        (re.compile(r"^M([2-8])(?!\d)"), "SERIE {0}"),         # M2, M3 COMPETITION...
    ],
    "MERCEDES-BENZ": [
        # A 200, CLASE C 220 D, AMG A 45, V 250 D, G 63...
        (re.compile(r"^(?:CLASE\s+)?([ABCEGSTV])\s?\d{2,3}(?!\d)"), "CLASE {0}"),
    ],
    "AUDI": [
        (re.compile(r"^(?:RS\s?|S)Q([2-8])"), "Q{0}"),                  # SQ5, RS Q3
        (re.compile(r"^(Q[468])\b.*E-TRON"), "{0} E-TRON"),              # Q4 40 E-TRON
        (re.compile(r"^(?:RS\s+)?E-TRON GT"), "E-TRON GT"),
        (re.compile(r"^(?:RS\s?|S)([3-8])(?!\d)"), "A{0}"),              # S3, RS 3, RS6
    ],
    "LEXUS": [
        (re.compile(r"^(UX|NX|RX|RZ|ES|IS|LS|LM|CT|RC|LC|GS)\s*\d{2,3}"), "{0}"),
    ],
}


def reparar_mojibake(texto: str) -> str:
    """Arregla letras acentuadas mal decodificadas ("CITROÃ\x8bN" -> "CITROËN").

    Leemos los TXT como latin-1 para que 1 byte = 1 carácter (así las posiciones
    fijas no se desplazan). Si el fichero guarda alguna letra en UTF-8, aparece
    como dos caracteres raros; aquí intentamos recomponerla.
    """
    if texto.isascii():
        return texto
    try:
        return texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto


def limpiar_texto(valor) -> str:
    """Mayúsculas, sin tildes, espacios simples. None/NaN -> ''."""
    if valor is None:
        return ""
    texto = str(valor)
    if texto.lower() == "nan":
        return ""
    texto = reparar_mojibake(texto)
    texto = unicodedata.normalize("NFKD", texto.upper().strip())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip()


def _leer_csv(ruta: Path):
    """Lee un CSV de catálogo ignorando líneas vacías y comentarios (#)."""
    with ruta.open(encoding="utf-8") as f:
        lineas = (l for l in f if l.strip() and not l.lstrip().startswith("#"))
        yield from csv.DictReader(lineas)


@dataclass(frozen=True)
class Clasificacion:
    marca: str
    modelo: str
    metodo: str           # catalogo | regla_serie | sin_modelo | sin_clasificar
    incluir: bool = True  # False = no es un turismo (furgoneta, autocaravana...)
    motivo_exclusion: str = ""


class Normalizador:
    def __init__(self, carpeta: Path = CARPETA_CATALOGOS):
        self.alias_marcas: dict[str, str] = {}
        self.accion_marca: dict[str, str] = {}      # marca normalizada -> excluir | base
        self.cambios_marca: list[tuple[str, str, str]] = []
        self.catalogo: dict[str, list[tuple[str, str, bool]]] = {}
        self._cargar(carpeta)

    # ------------------------------------------------------------------ carga
    def _cargar(self, carpeta: Path) -> None:
        for fila in _leer_csv(carpeta / "marcas.csv"):
            original = limpiar_texto(fila["marca_original"])
            destino = limpiar_texto(fila["marca_normalizada"])
            accion = fila["accion"].strip().lower()
            if accion == "alias":
                self.alias_marcas[original] = destino
            elif accion in {"excluir", "base"}:
                self.alias_marcas.setdefault(original, destino)
                self.accion_marca[destino] = accion
                self.accion_marca[original] = accion
            else:
                raise ValueError(f"accion desconocida en marcas.csv: {accion!r}")

        for fila in _leer_csv(carpeta / "cambios_marca.csv"):
            self.cambios_marca.append((limpiar_texto(fila["marca_origen"]),
                                       limpiar_texto(fila["prefijo_modelo"]),
                                       limpiar_texto(fila["marca_destino"])))

        for fila in _leer_csv(carpeta / "modelos.csv"):
            marca = limpiar_texto(fila["marca"])
            incluir = fila.get("incluir", "1").strip() != "0"
            self.catalogo.setdefault(marca, []).append(
                (limpiar_texto(fila["patron"]), limpiar_texto(fila["modelo"]), incluir))
        # Los patrones más largos primero: "C3 AIRCROSS" debe probarse antes que "C3".
        for marca, filas in self.catalogo.items():
            filas.sort(key=lambda t: len(t[0]), reverse=True)

        # Índice modelo -> marca, solo para modelos cuyo nombre es único de una marca.
        # Sirve para rescatar registros sin marca ("DS 7 BLUEHDI" -> DS).
        por_modelo: dict[str, set[str]] = {}
        for marca, filas in self.catalogo.items():
            for patron, _, _ in filas:
                if len(patron) >= 4:
                    por_modelo.setdefault(patron, set()).add(marca)
        self.marca_por_modelo = {p: next(iter(m)) for p, m in por_modelo.items() if len(m) == 1}

    # ----------------------------------------------------------------- marcas
    def normalizar_marca(self, texto) -> tuple[str, str]:
        """Devuelve (marca_normalizada, accion). accion: incluir | excluir | base | sin_marca."""
        original = limpiar_texto(texto)
        if original in VALORES_VACIOS:
            return SIN_MARCA, "sin_marca"
        marca = self.alias_marcas.get(original, original)
        accion = self.accion_marca.get(marca) or self.accion_marca.get(original) or "incluir"
        return marca, accion

    # ----------------------------------------------------------------- modelos
    def _candidatos(self, marca: str, m: str) -> list[str]:
        """Variantes del texto del modelo que merece la pena probar, por orden."""
        prefijos = {marca, marca.replace("-", " ")} | PREFIJOS_MARCA_EXTRA.get(marca, set())
        prefijos |= RUIDO_INICIAL
        candidatos = [m]
        actual = m
        for _ in range(4):  # "CITROEN CITROEN C3": hay que quitar el prefijo varias veces
            nuevo = actual
            for p in sorted(prefijos, key=len, reverse=True):
                if nuevo.startswith(p + " "):
                    nuevo = nuevo[len(p) + 1:].lstrip()
                    break
            if marca == "PEUGEOT":
                nuevo = re.sub(r"^N\s?(?=\d{3,4}(?!\d))", "", nuevo)   # "N 2008" = nueva generación
            if nuevo == actual:
                break
            actual = nuevo
            candidatos.append(actual)
        if marca in MARCAS_CON_PREFIJO_E:
            candidatos += [c[2:] for c in list(candidatos) if c.startswith("E-")]
        if marca == "CITROEN":
            candidatos += [re.sub(r"^(C[345])X\b", r"\1 X", c) for c in list(candidatos)]
        vistos, unicos = set(), []
        for c in candidatos:
            if c and c not in vistos:
                vistos.add(c)
                unicos.append(c)
        return unicos

    @staticmethod
    def _coincide(m: str, patron: str, truncado: bool) -> bool:
        if patron.startswith("*"):          # "*BERLINGO": el texto CONTIENE la palabra
            return patron[1:] in m
        if m.startswith(patron):
            n = len(patron)
            return len(m) == n or m[n] in SEPARADORES
        return truncado and len(m) >= 5 and patron.startswith(m)

    def clasificar(self, marca_texto, modelo_texto, marca_base_texto="") -> Clasificacion:
        """Punto de entrada: marca y modelo sucios -> Clasificacion limpia."""
        marca, accion = self.normalizar_marca(marca_texto)
        modelo_original = limpiar_texto(modelo_texto)

        # Transformadores (p. ej. conversiones a GLP): la marca real es la del vehículo base.
        if accion == "base":
            base, accion_base = self.normalizar_marca(marca_base_texto)
            if base != SIN_MARCA and accion_base != "base":
                marca, accion = base, accion_base
            else:
                inferida = self._marca_desde_modelo(modelo_original)
                if inferida:
                    marca, accion = inferida, "incluir"
                else:
                    return Clasificacion(marca, modelo_original or SIN_MODELO, "sin_clasificar",
                                         False, "transformador_sin_marca_base")
        elif marca == SIN_MARCA:
            base, accion_base = self.normalizar_marca(marca_base_texto)
            inferida = base if base != SIN_MARCA else self._marca_desde_modelo(modelo_original)
            if inferida:
                marca, accion = inferida, "incluir"

        if accion == "excluir":
            return Clasificacion(marca, modelo_original or SIN_MODELO, "sin_clasificar",
                                 False, "marca_no_turismo")
        if modelo_original in VALORES_VACIOS:
            return Clasificacion(marca, SIN_MODELO, "sin_modelo")

        # Algunas marcas se registran bajo otra (CUPRA bajo SEAT, ABARTH bajo FIAT).
        for origen, prefijo, destino in self.cambios_marca:
            if marca == origen and (modelo_original == prefijo or modelo_original.startswith(prefijo + " ")):
                marca = destino
                modelo_original = modelo_original[len(prefijo):].strip() or modelo_original
                break

        if any(palabra in modelo_original for palabra in PALABRAS_NO_TURISMO):
            return Clasificacion(marca, modelo_original, "sin_clasificar", False, "modelo_no_turismo")

        truncado = len(limpiar_texto(modelo_texto)) >= LONGITUD_CAMPO_MODELO
        for candidato in self._candidatos(marca, modelo_original):
            for regla, plantilla in REGLAS_SERIE.get(marca, ()):
                coincidencia = regla.match(candidato)
                if coincidencia:
                    return Clasificacion(marca, plantilla.format(*coincidencia.groups()), "regla_serie")
            for patron, canonico, incluir in self.catalogo.get(marca, ()):
                if self._coincide(candidato, patron, truncado):
                    motivo = "" if incluir else "modelo_no_turismo"
                    return Clasificacion(marca, canonico, "catalogo", incluir, motivo)
        return Clasificacion(marca, f"OTROS {marca}", "sin_clasificar")

    def _marca_desde_modelo(self, modelo: str) -> str:
        """Si el modelo empieza por un nombre exclusivo de una marca, devuelve esa marca."""
        for longitud in range(min(len(modelo), 25), 3, -1):
            trozo = modelo[:longitud]
            if trozo in self.marca_por_modelo and (len(modelo) == longitud or modelo[longitud] in SEPARADORES):
                return self.marca_por_modelo[trozo]
        return ""
