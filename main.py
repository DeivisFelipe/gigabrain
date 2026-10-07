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
    python main.py entradas               lista os projetos do dataset EntradasGigabrain
    python main.py avaliar 2008-keepass   processa a reunião e compara com o gabarito
    python main.py avaliar todos --simulado      roda o dataset inteiro (conselho x agente único)

Opção global --dados <pasta> escolhe onde fica o banco (padrão: dados/).

Para usar a DeepSeek:  set DEEPSEEK_API_KEY=sua-chave   (PowerShell: $env:DEEPSEEK_API_KEY="...")
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys

from gigabrain import demo, entradas, reuniao
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


def cmd_entradas(args) -> None:
    for p in entradas.listar_projetos(args.entradas):
        print(f"{p['pasta']:<22} tier {p['qualidade_tier']}  {p['requisitos_dialogo']:>3} requisitos  "
              f"{p['palavras_transcricao']:>5} palavras  {p['nome_projeto']}")


def _formatar(v) -> str:
    return "  -  " if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)


def cmd_avaliar(args) -> None:
    provedor = ProvedorSimulado() if args.simulado else criar_provedor("deepseek")
    projetos = [p["pasta"] for p in entradas.listar_projetos(args.entradas) if int(p["requisitos_dialogo"]) > 0]
    if args.projeto != "todos":
        projetos = [args.projeto]
    modos = {"ambos": [True, False], "conselho": [True], "agente_unico": [False]}[args.modo]
    raiz = os.path.join(RAIZ, "dados-avaliacao", provedor.nome)

    colunas = ["previstos", "gabarito", "acertos", "precisao", "revocacao", "f1", "classe", "subtipo", "rastreio", "sem_respaldo"]
    linhas = []
    print(f"{'projeto':<22} {'modo':<13} " + " ".join(f"{c[:9]:>9}" for c in colunas))
    for nome in projetos:
        projeto = entradas.carregar_projeto(nome, args.entradas)
        for com_conselho in modos:
            modo = "conselho" if com_conselho else "agente_unico"
            pasta = os.path.join(raiz, nome, modo)
            if os.path.exists(pasta):
                shutil.rmtree(pasta)
            ctx = abrir_contexto(pasta, provedor, ver_agentes=args.ver_agentes)
            try:
                relatorio = reuniao.processar_e_avaliar(ctx, projeto, pasta, com_conselho)
            finally:
                ctx.banco.fechar()
            m = relatorio["metricas"]
            linhas.append({"projeto": nome, "modo": modo, **m})
            print(f"{nome:<22} {modo:<13} " + " ".join(f"{_formatar(m[c]):>9}" for c in colunas))

    if len(linhas) > 1:
        print()
        for modo in dict.fromkeys(l["modo"] for l in linhas):
            do_modo = [l for l in linhas if l["modo"] == modo]
            media = {c: (sum(l[c] for l in do_modo if l[c] is not None) / max(1, sum(l[c] is not None for l in do_modo)))
                     for c in colunas}
            print(f"{'MÉDIA':<22} {modo:<13} " + " ".join(f"{_formatar(media[c]):>9}" for c in colunas))

    os.makedirs(raiz, exist_ok=True)
    with open(os.path.join(raiz, "resumo.csv"), "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=["projeto", "modo", *colunas])
        escritor.writeheader()
        escritor.writerows(linhas)
    print(f"\nResumo em {os.path.relpath(os.path.join(raiz, 'resumo.csv'), RAIZ)}. Para ver um projeto no painel:")
    print(f"python main.py --dados {os.path.relpath(os.path.join(raiz, projetos[0], 'conselho' if True in modos else 'agente_unico'), RAIZ)} painel")


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

    p = sub.add_parser("entradas", help="lista os projetos do dataset de teste")
    p.add_argument("--entradas", default=entradas.PASTA_PADRAO, help="pasta do repositório EntradasGigabrain")
    p.set_defaults(func=cmd_entradas)

    p = sub.add_parser("avaliar", help="processa reuniões do dataset e compara com o gabarito")
    p.add_argument("projeto", help="pasta do projeto (ex.: 2008-keepass) ou 'todos'")
    p.add_argument("--modo", choices=["ambos", "conselho", "agente_unico"], default="ambos")
    p.add_argument("--simulado", action="store_true", help="baseline por regras, sem API")
    p.add_argument("--ver-agentes", action="store_true")
    p.add_argument("--entradas", default=entradas.PASTA_PADRAO)
    p.set_defaults(func=cmd_avaliar)

    args = parser.parse_args()
    try:
        args.func(args)
    except ErroProvedor as exc:
        sys.exit(f"Erro: {exc}")


if __name__ == "__main__":
    main()
