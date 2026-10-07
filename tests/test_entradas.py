"""Testes da leitura do dataset, do processamento de reunião e da avaliação."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from gigabrain import avaliacao, entradas, reuniao
from gigabrain.conselho import abrir_contexto
from gigabrain.provedores import ProvedorSimulado

FALAS = [
    ("Analyst", "Okay, next topic: login. How should that work?"),
    ("Client", "Uh, users must log in with their email and a password of at least 8 characters."),
    ("Analyst", "Just to confirm, users must log in with their email and a password?"),
    ("EndUser", "The the search page should show results in under two seconds."),
    ("TechLead", "Sorry, I was on mute."),
]
GABARITO = [
    ("p:R1", "Users must log in with their email and a password of at least 8 characters.", "NFR", "SE", "T2"),
    ("p:R2", "The search page shall show results in under two seconds.", "NFR", "PE", "T4"),
    ("p:R3", "The system shall export reports to PDF.", "FR", None, "T4"),
]


def criar_dataset(pasta: str, falas=FALAS) -> None:
    """Monta um projeto mínimo no mesmo formato do EntradasGigabrain."""
    base = os.path.join(pasta, "teste-projeto")
    os.makedirs(base)
    texto, segmentos = "", []
    for i, (falante, fala) in enumerate(falas, start=1):
        texto += f"{falante}: "
        inicio = len(texto)
        texto += fala
        seg = {"id": f"T{i}", "start": inicio, "end": len(texto), "speaker": falante}
        ids = [g[0] for g in GABARITO if g[4] == f"T{i}"]
        if ids:
            seg["gt_ids"] = ids
        segmentos.append(seg)
        texto += "\n"
    with open(os.path.join(base, "transcricao_reuniao.txt"), "w", encoding="utf-8") as f:
        f.write(texto)
    gabarito = {
        "doc_id": "teste-projeto", "doc_title": "Projeto de teste",
        "input": {"segments": segmentos},
        "requirements": [
            {"id": i, "text": t, "class": c, "subtype": s, "trace": {"input_segments": [turno], "reachable": True}}
            for i, t, c, s, turno in GABARITO
        ],
    }
    with open(os.path.join(base, "gabarito_requisitos.json"), "w", encoding="utf-8") as f:
        json.dump(gabarito, f)
    with open(os.path.join(pasta, "indice_documentos.csv"), "w", encoding="utf-8") as f:
        f.write("id_documento,nome_projeto,requisitos_dialogo,qualidade_tier,palavras_transcricao,arquivo_transcricao\n")
        f.write("teste-projeto,Projeto de teste,3,A,40,teste-projeto/transcricao_reuniao.txt\n")


class TestAvaliacao(unittest.TestCase):
    def test_metricas(self):
        gabarito = [
            {"id": "g1", "texto": "Users must log in with email and password", "classe": "NFR", "subtipo": "SE", "turnos": ["T2"]},
            {"id": "g2", "texto": "Export reports to PDF format", "classe": "FR", "subtipo": None, "turnos": ["T4"]},
        ]
        previstos = [
            {"id": "R1", "texto": "users log in with email and password", "classe": "FR", "subtipo": None, "turnos": ["T2"]},
            {"id": "R2", "texto": "the dashboard has a dark theme", "classe": "NFR", "subtipo": "LF", "turnos": ["T9"]},
        ]
        turnos = [{"id": "T2", "texto": "users log in with email and password"}]
        r = avaliacao.avaliar(previstos, gabarito, turnos)["metricas"]
        self.assertEqual((r["acertos"], r["precisao"], r["revocacao"], r["f1"]), (1, 0.5, 0.5, 0.5))
        self.assertEqual(r["classe"], 0.0)       # previu FR, era NFR
        self.assertEqual(r["subtipo"], 0.0)
        self.assertEqual(r["rastreio"], 1.0)
        self.assertEqual(r["sem_respaldo"], 0.5)  # R2 cita um turno que não existe

    def test_pareamento_um_para_um(self):
        gabarito = [{"id": "g1", "texto": "export reports to pdf", "classe": "FR", "subtipo": None, "turnos": []}]
        previstos = [{"id": f"R{i}", "texto": "export reports to pdf", "classe": "FR", "subtipo": None, "turnos": []} for i in (1, 2)]
        r = avaliacao.avaliar(previstos, gabarito, [])["metricas"]
        self.assertEqual((r["acertos"], r["precisao"], r["revocacao"]), (1, 0.5, 1.0))


class TestReuniao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        criar_dataset(self.tmp.name)

    def test_carregar_projeto(self):
        self.assertEqual(entradas.listar_projetos(self.tmp.name)[0]["pasta"], "teste-projeto")
        p = entradas.carregar_projeto("teste-projeto", self.tmp.name)
        self.assertEqual(p["turnos"][1]["texto"], FALAS[1][1])
        self.assertEqual([g["turnos"] for g in p["gabarito"]], [["T2"], ["T4"], ["T4"]])

    def test_processar_e_avaliar(self):
        projeto = entradas.carregar_projeto("teste-projeto", self.tmp.name)
        for com_conselho in (True, False):
            pasta = os.path.join(self.tmp.name, "saida", str(com_conselho))
            ctx = abrir_contexto(pasta, ProvedorSimulado())
            self.addCleanup(ctx.banco.fechar)
            r = reuniao.processar_e_avaliar(ctx, projeto, pasta, com_conselho)

            # O Analyst (confirmação) e a fala fora do tópico não viram requisito.
            self.assertEqual(r["metricas"]["previstos"], 2)
            self.assertEqual(r["metricas"]["acertos"], 2)
            self.assertEqual(r["nao_encontrados"], ["p:R3"])
            self.assertTrue(os.path.exists(os.path.join(pasta, "avaliacao.json")))

            req = ctx.banco.obter_requisito("R1")
            self.assertIn({"tipo": "turno", "ref": "T2", "projeto": "teste-projeto"}, req["fontes"])
            # Só o modo conselho cria especialistas.
            self.assertEqual(bool(ctx.banco.listar_especialistas()), com_conselho)


class TestUmRequisitoPorVez(unittest.TestCase):
    """No modo conselho, o especialista recomenda e o Gêmeo decide, um rascunho por vez."""

    def test_duplicado_e_descartado_pelo_gemeo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repetida = "Users must log in with their email and a password of at least 8 characters."
        criar_dataset(tmp.name, FALAS[:2] + [("EndUser", repetida)])
        projeto = entradas.carregar_projeto("teste-projeto", tmp.name)

        resultados = {}
        for com_conselho in (True, False):
            pasta = os.path.join(tmp.name, "saida", str(com_conselho))
            ctx = abrir_contexto(pasta, ProvedorSimulado())
            self.addCleanup(ctx.banco.fechar)
            resultados[com_conselho] = (reuniao.processar(ctx, projeto, com_conselho), ctx)

        com, ctx = resultados[True]
        self.assertEqual(len(com["previstos"]), 1)
        self.assertEqual(len(com["descartados"]), 1)
        eventos = ctx.banco.listar_eventos(com["conversa_id"])
        decisoes = [e["conteudo"] for e in eventos if e["tipo"] == "decisao"]
        self.assertEqual([d["salvar"] for d in decisoes], [True, False])
        salvo = next(e for e in eventos if e["tipo"] == "requisito_salvo")
        self.assertEqual((salvo["de"], salvo["para"]), ("gemeo", "repositorio"))
        self.assertIn("gemeo", salvo["conteudo"]["decidido_por"])

        sem, _ = resultados[False]
        self.assertEqual(len(sem["previstos"]), 2)  # sem conselho, o duplicado passa


@unittest.skipUnless(os.path.exists(os.path.join(entradas.PASTA_PADRAO, "indice_documentos.csv")),
                     "dataset EntradasGigabrain não clonado ao lado do projeto")
class TestDatasetReal(unittest.TestCase):
    def test_todos_os_projetos_carregam(self):
        for p in entradas.listar_projetos():
            projeto = entradas.carregar_projeto(p["pasta"])
            self.assertTrue(projeto["turnos"], p["pasta"])


if __name__ == "__main__":
    unittest.main()
