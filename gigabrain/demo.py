"""Demonstração: roda conversas prontas com o provedor simulado.

Serve para popular o banco e ver o painel funcionando sem precisar de chave
de API. As conversas mostram os principais caminhos da arquitetura:

1. pedido novo que envolve dois temas (cria dois especialistas)
2. nova versão de um requisito existente (R1 v2)
3. requisito que depende de outro (R2 depende_de R1)
4. requisito que substitui outro (R3 substitui R1, e R2 entra em revisão)
5. conversa no modo reunião (não cria especialista, gera pendência)
6. um documento do projeto entregue a um especialista
"""

from __future__ import annotations

from .conselho import Conselho, abrir_contexto, alimentar_especialista
from .provedores import ProvedorSimulado

CONVERSAS = [
    ("pos_reuniao", [
        "Quero notificar o cliente quando o pagamento for confirmado",
        "O cliente recebe um e-mail em até 1 minuto após a confirmação do pagamento",
        "aprovado",
    ]),
    ("pos_reuniao", [
        "Atualizar o R1 para notificar também pagamentos recusados",
        "aprovado",
    ]),
    ("pos_reuniao", [
        "Relatório mensal de pagamentos confirmados, que depende do R1",
        "O financeiro precisa ver o total confirmado por dia",
        "aprovado",
    ]),
    ("pos_reuniao", [
        "Substituir o R1 por notificação dentro do app, sem e-mail",
        "aprovado",
    ]),
    ("reuniao", [
        "Cliente quer aplicar cupom de desconto no carrinho",
        "O cupom vale uma vez por cliente e tem data de validade",
        "aprovado",
    ]),
]

DOCUMENTO = """\
# Regras de pagamento (ata da reunião de 12/09)
- Pagamentos por Pix são confirmados na hora; boleto leva até 2 dias úteis.
- Pagamento recusado pode ser tentado de novo até 3 vezes.
- Estornos só podem ser feitos pelo financeiro.
"""


def rodar(pasta_dados: str, mostrar=print) -> None:
    for modo, falas in CONVERSAS:
        ctx = abrir_contexto(pasta_dados, ProvedorSimulado(), modo=modo, ver_agentes=True)
        conselho = Conselho(ctx)
        mostrar(f"\n=== Conversa {conselho.iniciar()} (modo {modo}) ===")
        for fala in falas:
            mostrar(f"PO> {fala}")
            resposta = conselho.falar(fala)
            mostrar(f"Gêmeo> {resposta['mensagem_ao_po']}\n")
        ctx.banco.fechar()

    ctx = abrir_contexto(pasta_dados, ProvedorSimulado(), ver_agentes=True)
    mostrar("\n=== Documento entregue ao especialista de pagamentos ===")
    alimentar_especialista(ctx, "pagamentos", "regras-pagamento.md", DOCUMENTO)
    ctx.banco.fechar()
