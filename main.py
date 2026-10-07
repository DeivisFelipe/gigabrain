"""GigaBrain: linha de comando.

Comandos:
    python main.py conversar              conversa com o Gêmeo Digital (DeepSeek)
    python main.py conversar --simulado   idem, sem chamar API (respostas por regras)
    python main.py demo                   roda conversas prontas e popula dados-demo/
    python main.py painel                 abre o painel visual no navegador
    python main.py requisitos             lista os requisitos
    python main.py trilha R3              mostra como se chegou no R3
    python main.py especialistas          lista os especialistas
    python main.py alimentar pagamentos ata.md   entrega um documento a um especialista

Opção global --dados <pasta> escolhe onde fica o banco (padrão: dados/).

Para usar a DeepSeek:  set DEEPSEEK_API_KEY=sua-chave   (PowerShell: $env:DEEPSEEK_API_KEY="...")
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from gigabrain import demo
from gigabrain.conselho import Conselho, abrir_contexto, alimentar_especialista
from gigabrain.provedores import ErroProvedor, ProvedorSimulado, criar_provedor

RAIZ = os.path.dirname(os.path.abspath(__file__))


def cmd_conversar(args) -> None:
    provedor = ProvedorSimulado() if args.simulado else criar_provedor("deepseek")
    ctx = abrir_contexto(args.dados, provedor, modo=args.modo, ver_agentes=not args.silencioso)
    conselho = Conselho(ctx)
    conversa = conselho.iniciar()

    print(f"=== GigaBrain · conversa {conversa} · modo {args.modo} · provedor {provedor.nome} ===")
    print("Descreva o que você quer. Responda 'aprovado' quando a proposta estiver boa.")
    print("Digite 'sair' para encerrar sem aprovar.\n")
    while True:
        try:
            fala = input("PO> ").strip()
        except (EOFError, KeyboardInterrupt):
            fala = "sair"
        if not fala:
            continue
        if fala.lower() in ("sair", "exit", "quit"):
            conselho.encerrar()
            print("Conversa encerrada sem aprovar.")
            break
        try:
            resposta = conselho.falar(fala)
        except Exception as exc:  # falha de API não derruba a conversa
            print(f"\n[erro: {exc}]\n")
            continue
        print(f"\nGêmeo> {resposta['mensagem_ao_po']}")
        for req in resposta.get("requisitos", []):
            print(f"\n  ## {req.get('titulo')}  ({req.get('tipo', 'negocio')}, temas: {', '.join(req.get('temas', []))})")
            print(f"  {req.get('historia')}")
            for crit in req.get("criterios_aceite", []):
                print(f"   - {crit}")
            if req.get("base"):
                print(f"  -> nova versão de {req['base']}")
            for lig in req.get("ligacoes", []):
                print(f"  -> {lig['tipo']} {lig['alvo']}: {lig.get('motivo', '')}")
        print()
        if resposta["acao"] == "aprovado":
            break
    ctx.banco.fechar()


def cmd_demo(args) -> None:
    pasta = args.dados if args.dados != "dados" else os.path.join(RAIZ, "dados-demo")
    if os.path.exists(pasta):
        shutil.rmtree(pasta)
    demo.rodar(pasta)
    print(f"\nPronto. Veja no painel: python main.py --dados {os.path.relpath(pasta, RAIZ)} painel")


def cmd_painel(args) -> None:
    from gigabrain.painel.servidor import servir
    servir(args.dados, args.porta, abrir_navegador=not args.sem_navegador)


def cmd_requisitos(args) -> None:
    ctx = abrir_contexto(args.dados, ProvedorSimulado())
    for req in ctx.banco.listar_requisitos():
        print(f"{req['id']:>4}  v{req['versao_atual']}  {req['status']:<12} {req['titulo']}  [{', '.join(req['temas'])}]")
    for lig in ctx.banco.listar_ligacoes():
        print(f"      {lig['origem_id']} {lig['tipo']} {lig['destino_id']}")


def cmd_trilha(args) -> None:
    ctx = abrir_contexto(args.dados, ProvedorSimulado())
    for passo in ctx.banco.trilha(args.requisito):
        if passo["evento"] == "versao":
            print(f"{passo['momento']}  {passo['requisito_id']} v{passo['versao']}  {passo['titulo']}  ({passo['motivo']})")
        else:
            print(f"{passo['momento']}  {passo['requisito_id']} {passo['tipo']} {passo['destino_id']}  ({passo['motivo']})")


def cmd_especialistas(args) -> None:
    ctx = abrir_contexto(args.dados, ProvedorSimulado())
    for esp in ctx.banco.listar_especialistas():
        print(f"{esp['id']:<16} v{esp['versao_conhecimento']}  {esp['consultas']} consulta(s)  {esp['arquivo']}")


def cmd_alimentar(args) -> None:
    provedor = ProvedorSimulado() if args.simulado else criar_provedor("deepseek")
    ctx = abrir_contexto(args.dados, provedor, ver_agentes=True)
    with open(args.arquivo, encoding="utf-8") as f:
        versao = alimentar_especialista(ctx, args.especialista, os.path.basename(args.arquivo), f.read())
    print(f"Conhecimento de {args.especialista}: " + (f"versão {versao}" if versao else "sem mudanças"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="GigaBrain: conselho multiagente para requisitos")
    parser.add_argument("--dados", default="dados", help="pasta do banco, logs e conhecimento")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("conversar", help="conversa com o Gêmeo Digital")
    p.add_argument("--simulado", action="store_true", help="não chama API; usa respostas por regras")
    p.add_argument("--modo", choices=["pos_reuniao", "reuniao"], default="pos_reuniao")
    p.add_argument("--silencioso", action="store_true", help="não mostra a conversa interna dos agentes")
    p.set_defaults(func=cmd_conversar)

    p = sub.add_parser("demo", help="roda conversas prontas com o provedor simulado")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("painel", help="painel visual no navegador")
    p.add_argument("--porta", type=int, default=8765)
    p.add_argument("--sem-navegador", action="store_true")
    p.set_defaults(func=cmd_painel)

    sub.add_parser("requisitos").set_defaults(func=cmd_requisitos)
    p = sub.add_parser("trilha")
    p.add_argument("requisito")
    p.set_defaults(func=cmd_trilha)
    sub.add_parser("especialistas").set_defaults(func=cmd_especialistas)

    p = sub.add_parser("alimentar", help="entrega um documento do projeto a um especialista")
    p.add_argument("especialista")
    p.add_argument("arquivo")
    p.add_argument("--simulado", action="store_true")
    p.set_defaults(func=cmd_alimentar)

    args = parser.parse_args()
    try:
        args.func(args)
    except ErroProvedor as exc:
        sys.exit(f"Erro: {exc}")


if __name__ == "__main__":
    main()
