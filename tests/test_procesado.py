"""Prueba de extremo a extremo con ficheros TXT sintéticos (no hace falta descargar nada)."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import procesar_matriculaciones as pm  # noqa: E402


def linea(**campos) -> str:
    """Construye una línea de 714 caracteres con los campos en sus posiciones."""
    base = {"FEC_MATRICULA": "15012020", "CLAVE_TRAMITE": "1", "IND_NUEVO_USADO": "N",
            "CATEGORIA_HOMOLOGACION_UE": "M1", "CARROCERIA": "AB", "SERVICIO": "B00",
            "CATEGORIA_VEHICULO_ELECTRICO": ""}
    base.update(campos)
    texto = [" "] * pm.LONGITUD_REGISTRO
    for nombre, valor in base.items():
        ini, fin = pm.LAYOUT[nombre]
        texto[ini:fin] = list(str(valor).ljust(fin - ini)[:fin - ini])
    return "".join(texto)


class TestExtremoAExtremo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.origen = Path(self.tmp.name) / "raw"
        self.salida = Path(self.tmp.name) / "out"
        self.origen.mkdir()
        self._anios = pm.ANIOS
        pm.ANIOS = range(2020, 2021)             # un solo año basta para probar
        for mes in range(1, 13):
            lineas = ["Vehículos matriculados (cabecera)"]
            if mes == 1:
                lineas += self.lineas_enero()
            (self.origen / pm.nombre_fichero(2020, mes)).write_text("\n".join(lineas) + "\n", encoding="latin-1")

    def tearDown(self):
        pm.ANIOS = self._anios
        self.tmp.cleanup()

    @staticmethod
    def lineas_enero():
        peugeot = dict(MARCA_ITV="PEUGEOT", MODELO_ITV="N 2008 ALLURE", CODIGO_ITV="ABC123456")
        return [
            linea(**peugeot, CATEGORIA_VEHICULO_ELECTRICO="HEV"),                       # 1 turismo válido
            linea(**peugeot, CATEGORIA_VEHICULO_ELECTRICO="HEV"),                       # 2
            linea(**peugeot),                                                           # 3
            linea(MARCA_ITV="PEUGEOT", MODELO_ITV="MODELO RARO XYZ", CODIGO_ITV="ABC123456"),  # se rescata por CODIGO_ITV
            linea(**peugeot, IND_NUEVO_USADO="U"),                                      # usado -> fuera
            linea(**peugeot, CATEGORIA_HOMOLOGACION_UE="N1"),                           # furgoneta -> fuera
            linea(**peugeot, CLAVE_TRAMITE="2"),                                        # transferencia -> fuera
            linea(**peugeot, CARROCERIA="SA"),                                          # autocaravana -> fuera
            linea(**peugeot, SERVICIO="A07"),                                           # ambulancia -> fuera
            linea(MARCA_ITV="FORD", MODELO_ITV="TRANSIT CUSTOM"),                       # furgoneta por modelo -> fuera
            linea(MARCA_ITV="VW", MODELO_ITV="GOLF", CATEGORIA_VEHICULO_ELECTRICO="BEV"),
            "corta",                                                                    # longitud inválida
            linea(**peugeot, FEC_MATRICULA="99999999"),                                 # fecha inválida
        ]

    def ejecutar(self):
        with contextlib.redirect_stdout(io.StringIO()):
            pm.procesar(self.origen, self.salida)
        return pd.read_csv(self.salida / "matriculaciones_turismos.csv", keep_default_na=False)

    def test_resultado(self):
        df = self.ejecutar()
        self.assertEqual(int(df.matriculaciones.sum()), 5)       # 4 Peugeot + 1 Golf
        p = df[df.marca == "PEUGEOT"].groupby(["modelo_normalizado", "tecnologia"]).matriculaciones.sum()
        self.assertEqual(p[("2008", "Híbrido no enchufable")], 2)
        self.assertEqual(p[("2008", pm.SIN_ETIQUETA)], 2)         # el 4º entra por CODIGO_ITV
        golf = df[df.marca == "VOLKSWAGEN"].iloc[0]
        self.assertEqual((golf.modelo_normalizado, golf.tecnologia), ("GOLF", "Eléctrico puro"))

    def test_el_rescate_por_codigo_itv_queda_registrado(self):
        self.ejecutar()
        aud = pd.read_csv(self.salida / "auditoria_modelos.csv", keep_default_na=False)
        fila = aud[aud.modelo_original == "MODELO RARO XYZ"].iloc[0]
        self.assertEqual((fila.modelo_normalizado, fila.metodo), ("2008", "codigo_itv"))
        pend = pd.read_csv(self.salida / "modelos_pendientes_revision.csv", keep_default_na=False)
        self.assertEqual(len(pend), 0)

    def test_contadores_de_descartes(self):
        self.ejecutar()
        r = pd.read_csv(self.salida / "resumen_procesamiento.csv").iloc[0]
        self.assertEqual(r.longitud_invalida, 1)
        self.assertEqual(r.no_M1, 1)
        self.assertEqual(r.no_nuevo, 1)
        self.assertEqual(r.no_ordinario, 1)
        self.assertEqual(r.fecha_invalida, 1)
        self.assertEqual(r.excluidos_no_turismo, 3)               # SA, A07 y Transit
        exc = pd.read_csv(self.salida / "registros_excluidos.csv")
        self.assertEqual(set(exc.motivo.str.split(":").str[0]), {"carroceria_especial", "servicio", "modelo_no_turismo"})

    def test_falta_un_fichero(self):
        (self.origen / pm.nombre_fichero(2020, 7)).unlink()
        with self.assertRaises(FileNotFoundError):
            pm.procesar(self.origen, self.salida)

    def test_perfilar_no_falla(self):
        pm.ANIOS = range(2020, 2021)
        (self.origen / pm.nombre_fichero(2020, 6)).write_text("\n".join(self.lineas_enero()) + "\n", encoding="latin-1")
        with contextlib.redirect_stdout(io.StringIO()):
            pm.perfilar(self.origen, self.salida)
        perfil = pd.read_csv(self.salida / "perfil_campos.csv", keep_default_na=False)
        self.assertIn("SA", set(perfil[perfil.campo == "CARROCERIA"].valor))


if __name__ == "__main__":
    unittest.main()
