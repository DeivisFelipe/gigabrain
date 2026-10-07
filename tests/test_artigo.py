"""Testes da infraestrutura de avaliação para o artigo: modos, cenário com várias
reuniões, estatística e especialista de qualidade (tudo offline, com o simulador)."""

from __future__ import annotations

import os
import tempfile
import unittest

from gigabrain import cenarios, entradas, estatistica, reuniao
from gigabrain.conselho import abrir_contexto
from gigabrain.provedores import ProvedorSimulado
from tests.test_entradas import criar_dataset

FALAS = [
    ("Analyst", "Okay, next topic: login. How should that work?"),
    ("Client", "Users must log in with their email and a password of at least 8 characters."),
    ("Analyst", "So, let's talk about search. What do you need there?"),
    ("EndUser", "The search page should show results in under 2 seconds for every query."),
    ("Analyst", "Okay, next topic: reports. How should that work?"),
    ("Client", "The system shall export monthly reports to PDF for the finance team."),
    ("Analyst", "So, let's talk about backup. What do you need there?"),
    ("TechLead", "The database must be backed up every 24 hours to an external server."),
]
GABARITO = [
    ("p:R1", FALAS[1][1], "NFR", "SE", "T2"),
    ("p:R2", FALAS[3][1], "NFR", "PE", "T4"),
    ("p:R3", FALAS[5][1], "FR", None, "T6"),
    ("p:R4", FALAS[7][1], "NFR", "FT", "T8"),
]


class TestEstatistica(unittest.TestCase):
    def test_wilcoxon_igual_ao_scipy(self):
        # scipy.stats.wilcoxon(a, b) -> p = 0.0390625 (exato)
        a = [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30]
        b = [0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29]
        r = estatistica.wilcoxon_pareado(a, b)
        self.assertAlmostEqual(r["p"], 0.0391, places=4)
        self.assertEqual((r["w_mais"], r["w_menos"]), (40.0, 5.0))

    def test_resumir_usa_media_das_repeticoes(self):
        linhas = [{"projeto": p, "modo": m, "rep": r, "f1": v}
                  for p, base in (("a", 0.8), ("b", 0.6), ("c", 0.7))
                  for m, extra in (("conselho", 0.1), ("agente_unico", 0.0))
                  for r, v in ((1, base + extra), (2, base + extra + 0.02))]
        resumo = estatistica.resumir(linhas)
        self.assertAlmostEqual(resumo["por_modo"]["conselho"]["f1"]["media"], 0.81, places=4)
        comp = next(c for c in resumo["comparacoes"] if c["metrica"] == "f1")
        self.assertEqual((comp["a"], comp["b"], comp["efeito"]), ("conselho", "agente_unico", 1.0))


class TestAnotacao(unittest.TestCase):
    def test_kappa(self):
        import csv
        from gigabrain import anotacao
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        caminho = os.path.join(tmp.name, "a.csv")
        a1 = ["c", "c", "c", "i", "c", "i", "c", "c"]
        a2 = ["c", "c", "i", "i", "c", "i", "c", "sim"]  # "sim" também vale como correta
        with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=anotacao.COLUNAS, delimiter=";")
            w.writeheader()
            for i, (x, y) in enumerate(zip(a1, a2)):
                w.writerow({"id": i, "anotador_1": x, "anotador_2": y})
        r = anotacao.kappa(caminho)
        # concordância 7/8; esperada = 6/8*5/8 + 2/8*3/8 = 0.5625 -> kappa = (0.875-0.5625)/(1-0.5625)
        self.assertEqual(r["anotadas"], 8)
        self.assertAlmostEqual(r["kappa"], round((0.875 - 0.5625) / (1 - 0.5625), 4))


class TestModosECenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        criar_dataset(self.tmp.name, FALAS)
        # criar_dataset usa o gabarito padrão; troca pelo destes testes
        import json
        caminho = os.path.join(self.tmp.name, "teste-projeto", "gabarito_requisitos.json")
        with open(caminho, encoding="utf-8") as f:
            g = json.load(f)
        g["requirements"] = [{"id": i, "text": t, "class": c, "subtype": s, "trace": {"input_segments": [tu], "reachable": True}}
                             for i, t, c, s, tu in GABARITO]
        for seg in g["input"]["segments"]:
            seg["gt_ids"] = [x[0] for x in GABARITO if x[4] == seg["id"]]
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(g, f)
        self.projeto = entradas.carregar_projeto("teste-projeto", self.tmp.name)

    def contexto(self, nome):
        ctx = abrir_contexto(os.path.join(self.tmp.name, nome), ProvedorSimulado())
        self.addCleanup(ctx.banco.fechar)
        return ctx

    def test_tres_modos_extraem_e_so_o_conselho_usa_especialistas(self):
        for modo in reuniao.MODOS:
            ctx = self.contexto(modo)
            r = reuniao.processar_e_avaliar(ctx, self.projeto, os.path.join(self.tmp.name, modo), modo)
            self.assertEqual(r["metricas"]["acertos"], 4, modo)
            especialistas = {e["id"] for e in ctx.banco.listar_especialistas()}
            if modo == "conselho":
                self.assertIn("qualidade", especialistas)  # especialista fixo de classe/subtipo
            else:
                self.assertEqual(especialistas, set(), modo)

    def test_cenario_e_deterministico_e_injeta_nas_sessoes_seguintes(self):
        c1 = cenarios.gerar_sessoes(self.projeto, n_sessoes=2, fracao=0.25)
        c2 = cenarios.gerar_sessoes(self.projeto, n_sessoes=2, fracao=0.25)
        self.assertEqual(c1, c2)
        self.assertEqual(len(c1["sessoes"]), 2)
        self.assertEqual({i["tipo"] for i in c1["injecoes"]}, {"repeticao", "mudanca"})
        for inj in c1["injecoes"]:
            self.assertEqual(inj["sessao"], 2)
            self.assertTrue(inj["turno"].startswith("T9"))

    def test_conselho_evita_repeticao_e_liga_mudanca(self):
        cenario = cenarios.gerar_sessoes(self.projeto, n_sessoes=2, fracao=0.25)
        resultados = {}
        for modo in ("conselho", "agente_unico"):
            ctx = self.contexto("sessoes-" + modo)
            r = reuniao.processar_sessoes_e_avaliar(ctx, self.projeto, cenario, os.path.join(self.tmp.name, "sessoes-" + modo), modo)
            resultados[modo] = r["metricas"]
            self.assertEqual(len(r["conversas"]), 2)  # uma conversa por reunião, mesmo repositório
        self.assertEqual(resultados["conselho"]["repeticoes_evitadas"], 1.0)
        self.assertEqual(resultados["agente_unico"]["repeticoes_evitadas"], 0.0)
        self.assertEqual(resultados["conselho"]["mudancas_ligadas"], 1.0)
        self.assertEqual(resultados["agente_unico"]["mudancas_ligadas"], 0.0)

    def test_conhecimento_anota_sem_llm_e_reorganiza_a_cada_cinco(self):
        ctx = self.contexto("conhecimento")
        reuniao.processar(ctx, self.projeto, "conselho")
        papeis = [e["conteudo"]["papel"] for e in ctx.banco.listar_eventos() if e["tipo"] == "chamada_llm"]
        self.assertNotIn("especialista_atualizar", papeis)  # 4 requisitos, temas diferentes: nenhum chega a 5 anotações
        motivos = [v["motivo"] for e in ctx.banco.listar_especialistas() for v in ctx.banco.versoes_conhecimento(e["id"])]
        self.assertIn("R1 salvo", motivos)


if __name__ == "__main__":
    unittest.main()
