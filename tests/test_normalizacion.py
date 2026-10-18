"""Pruebas de normalizacion.py. Ejecutar:  python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from normalizacion import Normalizador, limpiar_texto  # noqa: E402

N = Normalizador()


class TestLimpieza(unittest.TestCase):
    def test_tildes_y_espacios(self):
        self.assertEqual(limpiar_texto("  Citroën   C3 "), "CITROEN C3")

    def test_nan_y_none(self):
        self.assertEqual(limpiar_texto(None), "")
        self.assertEqual(limpiar_texto(float("nan")), "")

    def test_mojibake_utf8_leido_como_latin1(self):
        self.assertEqual(limpiar_texto("CITRO\xc3\x8bN"), "CITROEN")


class TestMarcas(unittest.TestCase):
    def test_alias(self):
        for texto, esperado in [("VW", "VOLKSWAGEN"), ("MERCEDES BENZ AG", "MERCEDES-BENZ"),
                                ("BAYER MOT WERKE-BMW", "BMW"), ("M.G.", "MG"), ("EMG", "MG")]:
            self.assertEqual(N.normalizar_marca(texto)[0], esperado, texto)

    def test_marca_vacia(self):
        self.assertEqual(N.normalizar_marca("-")[1], "sin_marca")

    def test_no_se_fusionan_ssangyong_y_kg_mobility(self):
        self.assertNotEqual(N.normalizar_marca("SSANGYONG")[0], N.normalizar_marca("KG MOBILITY")[0])


class TestModelos(unittest.TestCase):
    def assertModelo(self, marca, modelo, esperado_marca, esperado_modelo, metodo=None):
        cl = N.clasificar(marca, modelo)
        self.assertEqual((cl.marca, cl.modelo), (esperado_marca, esperado_modelo), f"{marca} / {modelo}")
        if metodo:
            self.assertEqual(cl.metodo, metodo)

    def test_peugeot_nueva_generacion_y_electricos(self):
        self.assertModelo("PEUGEOT", "N 2008 ALLURE PURETECH", "PEUGEOT", "2008")
        self.assertModelo("PEUGEOT", "N 3008 GT BLUEHDI 130", "PEUGEOT", "3008")
        self.assertModelo("PEUGEOT", "E-208 5P ALLURE ELECTR", "PEUGEOT", "208")
        self.assertModelo("PEUGEOT", "E-2008 ALLURE ELECTRIC", "PEUGEOT", "2008")

    def test_prefijo_de_marca_repetido(self):
        self.assertModelo("CITROEN", "CITROEN CITROEN C3 TURBO", "CITROEN", "C3")
        self.assertModelo("CITROEN", "SUV C5 AIRCROSS BLUEHD", "CITROEN", "C5 AIRCROSS")

    def test_limite_de_palabra_c3_no_es_c30(self):
        self.assertFalse(Normalizador._coincide("C30", "C3", False))
        self.assertTrue(Normalizador._coincide("C3 PURETECH", "C3", False))

    def test_nombre_cortado_por_el_limite_de_22_caracteres(self):
        self.assertTrue(Normalizador._coincide("C3 AIRCR", "C3 AIRCROSS", True))
        self.assertFalse(Normalizador._coincide("C3 AIRCR", "C3 AIRCROSS", False))

    def test_berlingo_mal_escrito(self):
        self.assertModelo("CITROEN", "NUEVO BERLINGOATALLA M", "CITROEN", "BERLINGO")

    def test_bmw_series(self):
        for texto, serie in [("120D", "SERIE 1"), ("M135I XDRIVE", "SERIE 1"), ("225XE IPERFORMANCE", "SERIE 2"),
                             ("330E", "SERIE 3"), ("M440I XDRIVE", "SERIE 4"), ("540D XDRIVE", "SERIE 5")]:
            self.assertModelo("BMW", texto, "BMW", serie, "regla_serie")
        self.assertModelo("BMW", "X1 SDRIVE18D", "BMW", "X1")

    def test_mercedes(self):
        self.assertModelo("MERCEDES-BENZ", "A 200", "MERCEDES-BENZ", "CLASE A", "regla_serie")
        self.assertModelo("MERCEDES-BENZ", "CLASE C 220 D", "MERCEDES-BENZ", "CLASE C")
        self.assertModelo("MERCEDES-BENZ", "AMG GLC 43 4MATIC", "MERCEDES-BENZ", "GLC")
        self.assertModelo("MERCEDES-BENZ", "GLC 220 D", "MERCEDES-BENZ", "GLC")   # no debe ser "CLASE G"
        self.assertModelo("MERCEDES-BENZ", "EQA 250", "MERCEDES-BENZ", "EQA")
        self.assertModelo("MERCEDES-BENZ", "V 250 D", "MERCEDES-BENZ", "CLASE V")

    def test_audi(self):
        self.assertModelo("AUDI", "Q4 40 E-TRON", "AUDI", "Q4 E-TRON")
        self.assertModelo("AUDI", "RS Q3 SPORTBACK", "AUDI", "Q3")
        self.assertModelo("AUDI", "A3 SPORTBACK 35 TFSI", "AUDI", "A3")
        self.assertModelo("AUDI", "Q3 SPORTBACK", "AUDI", "Q3")

    def test_marcas_registradas_bajo_otra(self):
        self.assertModelo("SEAT", "CUPRA ATECA", "CUPRA", "ATECA")
        self.assertModelo("FIAT", "ABARTH 500", "ABARTH", "500")

    def test_el_nombre_de_la_marca_forma_parte_del_modelo(self):
        self.assertModelo("DS", "DS 7 CROSSBACK BLUEHDI", "DS", "DS 7")
        self.assertModelo("MG", "MG ZS EV", "MG", "MG ZS")

    def test_furgonetas_se_excluyen(self):
        for marca, modelo in [("FORD", "TRANSIT CUSTOM 300"), ("MERCEDES-BENZ", "SPRINTER 314"),
                              ("RENAULT", "MASTER L2H2"), ("MERCEDES-BENZ", "SPRINTER 910 AMBULANCI")]:
            self.assertFalse(N.clasificar(marca, modelo).incluir, f"{marca} {modelo}")

    def test_monovolumenes_de_pasajeros_se_mantienen(self):
        self.assertTrue(N.clasificar("TOYOTA", "PROACE CITY VERSO").incluir)
        self.assertTrue(N.clasificar("OPEL", "ZAFIRA LIFE").incluir)
        self.assertTrue(N.clasificar("MERCEDES-BENZ", "V 250 D").incluir)

    def test_transformador_usa_marca_base(self):
        cl = N.clasificar("OLEOPEL", "CORSA", marca_base_texto="OPEL")
        self.assertEqual((cl.marca, cl.modelo, cl.incluir), ("OPEL", "CORSA", True))

    def test_transformador_sin_marca_base_infiere_por_el_modelo(self):
        cl = N.clasificar("OLEOPEL", "CORSA")
        self.assertEqual((cl.marca, cl.modelo), ("OPEL", "CORSA"))

    def test_marca_vacia_se_recupera_por_el_modelo(self):
        cl = N.clasificar("", "DS 7 BLUEHDI 130 AUTOM")
        self.assertEqual((cl.marca, cl.modelo), ("DS", "DS 7"))

    def test_modelo_vacio(self):
        self.assertEqual(N.clasificar("TOYOTA", "-").metodo, "sin_modelo")

    def test_desconocido_va_a_otros(self):
        cl = N.clasificar("FORD", "MODELO INVENTADO XYZ")
        self.assertEqual((cl.modelo, cl.metodo), ("OTROS FORD", "sin_clasificar"))


if __name__ == "__main__":
    unittest.main()
