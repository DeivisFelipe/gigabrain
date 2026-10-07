"""Testes do GigaBrain (rodam offline, com o provedor simulado).

    python -m unittest discover tests
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import unittest

from gigabrain import conhecimento
from gigabrain.agentes import Especialista
from gigabrain.aprovacao import eh_aprovacao
from gigabrain.banco import SCHEMA, Banco
from gigabrain.conselho import Conselho, abrir_contexto, alimentar_especialista
from gigabrain.provedores import ProvedorSimulado


def requisito(titulo: str, temas=("pagamentos",)) -> dict:
    return {"titulo": titulo, "historia": f"Como cliente, quero {titulo}", "criterios_aceite": ["c1"], "tipo": "negocio", "temas": list(temas)}


class TestAprovacao(unittest.TestCase):
    def test_aceita_aprovacoes_explicitas(self):
        for texto in ["aprovado", "Aprovado!", "tá bom assim!", "pode fechar", "/aprovar", "  APROVO  "]:
            self.assertTrue(eh_aprovacao(texto), texto)

    def test_recusa_aprovacao_com_ressalva(self):
        for texto in ["Aprovado, mas troca o critério 2", "aprovo não", "aprovado exceto o prazo", "aprovar depois", "ok", ""]:
            self.assertFalse(eh_aprovacao(texto), texto)


class TestBanco(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.banco = Banco(self.tmp.name)
        self.addCleanup(self.banco.fechar)

    def test_versoes_e_ligacoes(self):
        r1 = self.banco.salvar_requisito(requisito("notificar pagamento"), None)
        self.assertEqual((r1["requisito_id"], r1["versao"]), ("R1", 1))

        r2 = self.banco.salvar_requisito(requisito("relatório"), None, ligacoes=[{"tipo": "depende_de", "alvo": "R1"}])
        self.assertEqual(r2["ligacoes"], [{"origem": "R2", "tipo": "depende_de", "destino": "R1"}])

        # Nova versão do R1 coloca o R2 (que depende dele) em revisão.
        v2 = self.banco.salvar_requisito(requisito("notificar pagamento e recusa"), None, base_id="R1", motivo="incluir recusas")
        self.assertEqual(v2["versao"], 2)
        self.assertEqual(v2["em_revisao"], ["R2"])
        self.assertEqual(self.banco.obter_requisito("R2")["status"], "em_revisao")

        # Substituir o R1 muda o status dele.
        self.banco.salvar_requisito(requisito("notificação no app"), None, ligacoes=[{"tipo": "substitui", "alvo": "R1"}])
        self.assertEqual(self.banco.obter_requisito("R1")["status"], "substituido")

        trilha = self.banco.trilha("R3")
        self.assertEqual([(p["requisito_id"], p["evento"]) for p in trilha],
                         [("R1", "versao"), ("R1", "versao"), ("R3", "versao"), ("R3", "ligacao")])

    def test_ignora_ligacao_para_requisito_inexistente(self):
        r = self.banco.salvar_requisito(requisito("x"), None, ligacoes=[{"tipo": "refina", "alvo": "R99"}])
        self.assertEqual(r["ligacoes"], [])

    def test_bancos_separados_e_migracao_do_formato_antigo(self):
        for arquivo in ("requisitos.db", "especialistas.db", "log.db"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, arquivo)), arquivo)

        # Pasta no formato antigo: tudo num gigabrain.db.
        antiga = os.path.join(self.tmp.name, "antiga")
        os.makedirs(antiga)
        con = sqlite3.connect(os.path.join(antiga, "gigabrain.db"))
        con.executescript(re.sub(r"(main|esp|log)[.]", "", SCHEMA))
        con.execute("INSERT INTO conversa VALUES ('c-1', '2026-01-01T00:00:00', NULL, 'pos_reuniao', 'aberta')")
        con.commit()
        con.close()
        banco = Banco(antiga)
        self.addCleanup(banco.fechar)
        self.assertEqual([c["id"] for c in banco.listar_conversas()], ["c-1"])
        self.assertTrue(os.path.exists(os.path.join(antiga, "gigabrain.db.migrado")))
        self.assertFalse(os.path.exists(os.path.join(antiga, "gigabrain.db")))

    def test_conhecimento_nao_duplica_versao_igual(self):
        self.banco.criar_especialista("pagamentos", "pagamentos", "d", "arq.md")
        self.assertEqual(self.banco.registrar_conhecimento("pagamentos", "a", "criação", {}), 1)
        self.assertIsNone(self.banco.registrar_conhecimento("pagamentos", "a", "de novo", {}))
        self.assertEqual(self.banco.registrar_conhecimento("pagamentos", "b", "mudou", {}), 2)


class TestConhecimento(unittest.TestCase):
    def test_adicionar_item_tira_o_vazio_e_nao_duplica(self):
        md = conhecimento.modelo_inicial("pagamentos", "d")
        md = conhecimento.adicionar_item(md, "Regras de negócio", "Pix confirma na hora")
        md = conhecimento.adicionar_item(md, "Regras de negócio", "Pix confirma na hora")
        self.assertEqual(conhecimento.itens(md, "Regras de negócio"), ["Pix confirma na hora"])
        self.assertEqual(conhecimento.itens(md, "Glossário"), [])


class TestFluxo(unittest.TestCase):
    def setUp(self):
        # addCleanup roda na ordem inversa: o banco fecha antes de apagar a pasta.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def conselho(self, modo="pos_reuniao"):
        ctx = abrir_contexto(self.tmp.name, ProvedorSimulado(), modo=modo)
        conselho = Conselho(ctx)
        conselho.iniciar()
        self.addCleanup(ctx.banco.fechar)
        return conselho

    def test_conversa_completa(self):
        c = self.conselho()
        r = c.falar("Quero notificar o cliente quando o pagamento for confirmado")
        self.assertEqual(r["acao"], "perguntar")  # especialistas novos devolvem dúvidas ao PO
        self.assertEqual(c.falar("O cliente recebe e-mail em até 1 minuto")["acao"], "propor")

        # Aprovação com ressalva não salva nada.
        self.assertNotEqual(c.falar("aprovado, mas troca o prazo")["acao"], "aprovado")
        r = c.falar("aprovado")
        self.assertEqual(r["acao"], "aprovado")

        banco = c.ctx.banco
        req = banco.obter_requisito("R1")
        self.assertEqual(set(req["temas"]), {"pagamentos", "notificacoes"})
        self.assertIn({"tipo": "conversa", "ref": c.ctx.conversa_id}, req["fontes"])
        self.assertTrue(any(f["tipo"] == "especialista" for f in req["fontes"]))

        # Os dois especialistas foram criados e já registraram o R1.
        for id_ in ("pagamentos", "notificacoes"):
            md = conhecimento.ler(banco.obter_especialista(id_)["arquivo"])
            self.assertIn("R1", "\n".join(conhecimento.itens(md, "Requisitos aprovados")))

        # Tudo ficou no log, no banco e no arquivo JSONL.
        tipos = [e["tipo"] for e in banco.listar_eventos(c.ctx.conversa_id)]
        for tipo in ("pedido_po", "consulta", "decomposicao", "especialista_criado", "resposta_especialista",
                     "resposta_consolidada", "proposta", "aprovacao", "requisito_salvo", "conhecimento_atualizado"):
            self.assertIn(tipo, tipos)
        with open(os.path.join(self.tmp.name, "logs", f"{c.ctx.conversa_id}.jsonl"), encoding="utf-8") as f:
            linhas = [json.loads(l) for l in f]
        self.assertEqual(len(linhas), len(tipos))
        self.assertEqual(banco.listar_conversas()[0]["status"], "aprovada")

    def test_modo_reuniao_nao_cria_especialista(self):
        c = self.conselho(modo="reuniao")
        c.falar("Cliente quer cupom de desconto no carrinho")
        self.assertEqual(c.ctx.banco.listar_especialistas(), [])
        tipos = [e["tipo"] for e in c.ctx.banco.listar_eventos(c.ctx.conversa_id)]
        self.assertIn("pendencia", tipos)

    def test_edicao_manual_e_documento_viram_versoes(self):
        c = self.conselho()
        esp = Especialista.criar(c.ctx, "pagamentos", "Regras de pagamento")
        with open(esp.arquivo, "a", encoding="utf-8") as f:
            f.write("\n- nota escrita à mão\n")
        self.assertEqual(esp.sincronizar(), 2)

        versao = alimentar_especialista(c.ctx, "pagamentos", "ata.md", "- Estorno só pelo financeiro, sempre.")
        self.assertEqual(versao, 3)
        motivos = [v["motivo"] for v in c.ctx.banco.versoes_conhecimento("pagamentos")]
        self.assertEqual(motivos, ["criação", "edição manual", "documento ata.md"])


if __name__ == "__main__":
    unittest.main()
