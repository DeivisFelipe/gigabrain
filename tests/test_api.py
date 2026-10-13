"""Testes da API REST (provedor simulado, sem rede)."""

from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from gigabrain.api import criar_app
from gigabrain.provedores import ProvedorSimulado


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cliente = TestClient(criar_app(self.tmp.name, ProvedorSimulado))
        self.cliente.__enter__()
        self.addCleanup(self.cliente.__exit__, None, None, None)

    def test_conversa_completa_e_historico(self):
        c = self.cliente
        conversa = c.post("/api/v1/conversas", json={"modo": "pos_reuniao"}).json()
        cid = conversa["id"]

        r = c.post(f"/api/v1/conversas/{cid}/mensagens", json={"texto": "Quero notificar o cliente quando o pagamento for confirmado"}).json()
        self.assertEqual(r["acao"], "perguntar")  # especialistas novos devolvem dúvidas ao PO
        self.assertFalse(r["proposta_pendente"])
        tipos = {b["tipo"] for b in r["bastidores"]}
        self.assertTrue({"consulta", "especialista_criado", "resposta_consolidada"} <= tipos)

        # Sem proposta, o botão Aprovar não tem o que aprovar.
        self.assertEqual(c.post(f"/api/v1/conversas/{cid}/aprovar").status_code, 409)

        r = c.post(f"/api/v1/conversas/{cid}/mensagens", json={"texto": "O cliente recebe um e-mail em até 1 minuto"}).json()
        self.assertEqual(r["acao"], "propor")
        self.assertTrue(r["proposta_pendente"])
        self.assertEqual(len(r["requisitos_propostos"]), 1)

        r = c.post(f"/api/v1/conversas/{cid}/aprovar").json()
        self.assertEqual((r["acao"], r["salvos"], r["conversa_ativa"]), ("aprovado", ["R1"], False))
        self.assertIn("requisito_salvo", {b["tipo"] for b in r["bastidores"]})

        # Depois de aprovada, a conversa não aceita mais mensagens.
        self.assertEqual(c.post(f"/api/v1/conversas/{cid}/mensagens", json={"texto": "oi"}).status_code, 409)

        # Histórico da conversa, como o PO vê.
        h = c.get(f"/api/v1/conversas/{cid}").json()
        self.assertEqual(h["status"], "aprovada")
        self.assertEqual([m["autor"] for m in h["mensagens"]], ["po", "gemeo", "po", "gemeo", "po", "gemeo"])
        self.assertEqual(h["mensagens"][-1]["tipo"], "confirmacao")
        self.assertEqual(h["requisitos_salvos"], ["R1"])

        # Repositório de requisitos e bastidores.
        reqs = c.get("/api/v1/requisitos").json()
        self.assertEqual([r["id"] for r in reqs], ["R1"])
        self.assertEqual(c.get("/api/v1/requisitos", params={"busca": "pagamento"}).json()[0]["id"], "R1")
        detalhe = c.get("/api/v1/requisitos/R1").json()
        self.assertEqual(len(detalhe["versoes"]), 1)
        self.assertTrue(detalhe["trilha"])
        self.assertEqual(c.get("/api/v1/requisitos/R99").status_code, 404)
        eventos = c.get(f"/api/v1/conversas/{cid}/eventos", params={"completo": True}).json()
        self.assertTrue(all("conteudo" in e for e in eventos))
        esp = c.get("/api/v1/especialistas").json()
        self.assertTrue(esp)
        self.assertIn("conhecimento", c.get(f"/api/v1/especialistas/{esp[0]['id']}").json())

    def test_aprovar_escrevendo_e_encerrar(self):
        c = self.cliente
        cid = c.post("/api/v1/conversas").json()["id"]
        c.post(f"/api/v1/conversas/{cid}/mensagens", json={"texto": "Relatório mensal de vendas"})
        c.post(f"/api/v1/conversas/{cid}/mensagens", json={"texto": "Para o gerente comercial"})
        r = c.post(f"/api/v1/conversas/{cid}/mensagens", json={"texto": "aprovado"}).json()
        self.assertEqual(r["acao"], "aprovado")

        outra = c.post("/api/v1/conversas").json()["id"]
        self.assertEqual(c.post(f"/api/v1/conversas/{outra}/encerrar").json()["status"], "encerrada")
        conversas = {x["id"]: x for x in c.get("/api/v1/conversas").json()}
        self.assertEqual((conversas[outra]["status"], conversas[outra]["ativa"]), ("encerrada", False))

    def test_validacao_e_token(self):
        self.assertEqual(self.cliente.post("/api/v1/conversas/x/mensagens", json={"texto": ""}).status_code, 422)
        self.assertEqual(self.cliente.post("/api/v1/conversas/nao-existe/mensagens", json={"texto": "oi"}).status_code, 409)

        protegido = TestClient(criar_app(self.tmp.name, ProvedorSimulado, token="segredo"))
        self.assertEqual(protegido.get("/api/v1/requisitos").status_code, 401)
        self.assertEqual(protegido.get("/api/v1/requisitos", headers={"Authorization": "Bearer segredo"}).status_code, 200)
        self.assertEqual(protegido.get("/api/v1/saude").status_code, 200)  # saúde fica aberta


if __name__ == "__main__":
    unittest.main()
