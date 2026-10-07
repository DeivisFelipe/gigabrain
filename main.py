"""GigaBrain: linha de comando.

Comandos:
    python main.py conversar              conversa com o Gêmeo Digital (DeepSeek)
    python main.py conversar --simulado   idem, sem chamar API (respostas por regras)
    python main.py demo                   roda conversas prontas e popula dados-demo/
    python main.py painel                 painel visual com todas as pastas de dados (seletor no topo)
    python main.py requisitos             lista os requisitos
    python main.py trilha R3              mostra como se chegou no R3
    python main.py especialistas          lista os especialistas
    python main.py alimentar pagamentos ata.md   entrega um documento a um especialista
    python main.py entradas               lista os projetos do dataset EntradasGigabrain
    python main.py avaliar 2008-keepass   processa a reunião e compara com o gabarito
    python main.py avaliar todos --simulado      roda o dataset inteiro (conselho, agente único e LLM puro)
    python main.py avaliar todos --repeticoes 5  repete cada execução e compara com estatística
    python main.py avaliar todos --cenario sessoes   várias reuniões com repetições e mudanças injetadas
    python main.py anotacao exportar ligacoes.csv    amostra de ligações para anotação manual
    python main.py anotacao kappa ligacoes.csv       concordância entre os dois anotadores

Opção global --dados <pasta> escolhe onde fica o banco (padrão: dados/).

Para usar a DeepSeek:  set DEEPSEEK_API_KEY=sua-chave   (PowerShell: $env:DEEPSEEK_API_KEY="...")
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from gigabrain import anotacao, cenarios, demo, entradas, estatistica, reuniao
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
    # Sem --dados, o painel mostra todas as pastas de dados do projeto (com seletor).
    raiz = args.dados if args.dados != "dados" else RAIZ
    servir(raiz, args.porta, abrir_navegador=not args.sem_navegador)


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


COLUNAS = ["previstos", "gabarito", "acertos", "precisao", "revocacao", "f1", "classe", "subtipo", "rastreio", "sem_respaldo"]
COLUNAS_SESSOES = ["repeticoes_evitadas", "mudancas_capturadas", "mudancas_ligadas"]


def _pasta_execucao(raiz: str, cenario: str, projeto: str, modo: str, rep: int) -> str:
    return os.path.join(raiz, cenario, projeto, modo, f"rep-{rep}")


def _rodar_avaliacao(args, nome: str, modo: str, rep: int, raiz: str) -> dict:
    """Uma execução (projeto + modo + repetição), com pasta, bancos e provedor próprios."""
    provedor = ProvedorSimulado() if args.simulado else criar_provedor("deepseek")
    projeto = entradas.carregar_projeto(nome, args.entradas)
    pasta = _pasta_execucao(raiz, args.cenario, nome, modo, rep)
    if os.path.exists(pasta):
        shutil.rmtree(pasta)
    ctx = abrir_contexto(pasta, provedor, ver_agentes=args.ver_agentes)
    ctx.com_qualidade = not args.sem_qualidade
    try:
        if args.cenario == "sessoes":
            cenario = cenarios.gerar_sessoes(projeto, n_sessoes=args.sessoes)
            relatorio = reuniao.processar_sessoes_e_avaliar(ctx, projeto, cenario, pasta, modo)
        else:
            relatorio = reuniao.processar_e_avaliar(ctx, projeto, pasta, modo)
    finally:
        ctx.banco.fechar()
    return {"projeto": nome, "modo": modo, "rep": rep, **relatorio["metricas"]}


def _linha(rotulo: str, modo: str, valores: dict, colunas: list[str]) -> str:
    return f"{rotulo:<22} {modo:<13} " + " ".join(f"{_formatar(valores.get(c)):>9}" for c in colunas)


def cmd_avaliar(args) -> None:
    nome_provedor = ProvedorSimulado.nome if args.simulado else criar_provedor("deepseek").nome
    projetos = [p["pasta"] for p in entradas.listar_projetos(args.entradas) if int(p["requisitos_dialogo"]) > 0]
    if args.projeto != "todos":
        projetos = [args.projeto]
    modos = list(reuniao.MODOS) if args.modo == "todos" else args.modo.split(",")
    raiz = os.path.join(RAIZ, "dados-avaliacao", nome_provedor)
    reuniao.migrar_pastas_antigas(raiz)

    # Cada (projeto, modo, repetição) é independente: roda várias ao mesmo tempo.
    tarefas = [(nome, modo, rep) for rep in range(1, args.repeticoes + 1) for nome in projetos for modo in modos]
    colunas = COLUNAS + (COLUNAS_SESSOES if args.cenario == "sessoes" else [])
    linhas = []
    print(f"{len(tarefas)} execução(ões) · cenário {args.cenario} · {args.paralelo} ao mesmo tempo")
    print(f"{'projeto':<22} {'modo':<13} " + " ".join(f"{c[:9]:>9}" for c in colunas))
    with ThreadPoolExecutor(max_workers=args.paralelo) as executor:
        futuros = {executor.submit(_rodar_avaliacao, args, *t, raiz): t for t in tarefas}
        for futuro in as_completed(futuros):
            nome, modo, rep = futuros[futuro]
            try:
                linha = futuro.result()
            except Exception as exc:  # uma execução com erro não derruba as outras
                print(f"{nome:<22} {modo:<13} rep {rep} ERRO: {exc}")
                continue
            linhas.append(linha)
            print(_linha(f"{nome} #{rep}", modo, linha, colunas), flush=True)
    if not linhas:
        return
    linhas.sort(key=lambda l: (l["projeto"], reuniao.MODOS.index(l["modo"]), l["rep"]))

    resumo = estatistica.resumir(linhas)
    print()
    for modo, ms in resumo["por_modo"].items():
        print(_linha("MÉDIA", modo, {c: v["media"] for c, v in ms.items()}, colunas))
    if args.repeticoes > 1:
        for modo, ms in resumo["por_modo"].items():
            print(_linha("± entre repetições", modo, {c: v["desvio_entre_repeticoes"] for c, v in ms.items()}, colunas))
    if resumo["comparacoes"]:
        print("\nComparação pareada por projeto (Wilcoxon; efeito rank-biserial, >0 favorece o primeiro modo):")
        for c in resumo["comparacoes"]:
            marca = " *" if c["p"] < 0.05 else ""
            print(f"  {c['metrica']:<20} {c['a']} {c['media_a']:.3f} x {c['b']} {c['media_b']:.3f}"
                  f"  p={'<0.0001' if c['p'] < 0.0001 else format(c['p'], '.4f')}  efeito={c['efeito']:+.2f}  ({c['projetos']} projetos){marca}")

    pasta_cenario = os.path.join(raiz, args.cenario)
    os.makedirs(pasta_cenario, exist_ok=True)
    with open(os.path.join(pasta_cenario, "resumo.csv"), "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=["projeto", "modo", "rep", *colunas], extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(linhas)
    with open(os.path.join(pasta_cenario, "estatisticas.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=1)
    print(f"\nResumo em {os.path.relpath(pasta_cenario, RAIZ)} (resumo.csv e estatisticas.json). Painel: python main.py painel")


def cmd_anotacao(args) -> None:
    if args.acao == "exportar":
        n = anotacao.exportar(RAIZ, args.arquivo, args.amostra, provedor=args.provedor, cenario=args.cenario)
        print(f"{n} ligação(ões) sorteada(s) em {args.arquivo}. Cada anotador preenche a sua coluna com c (correta) ou i (incorreta).")
    else:
        r = anotacao.kappa(args.arquivo)
        if not r["anotadas"]:
            sys.exit("Nenhuma linha com as duas colunas de anotação preenchidas.")
        print(f"{r['anotadas']} ligações anotadas pelos dois · concordância {r['concordancia']:.0%} · kappa de Cohen {r['kappa']:.2f}")
        print(f"precisão das ligações: anotador 1 {r['precisao_anotador_1']:.0%} · anotador 2 {r['precisao_anotador_2']:.0%}"
              f" · consenso (os dois dizem correta) {r['precisao_consenso']:.0%}")


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
    p.add_argument("--modo", default="todos",
                   help="todos (padrão) ou lista separada por vírgula: conselho,agente_unico,llm_puro")
    p.add_argument("--repeticoes", type=int, default=1, help="quantas vezes rodar cada projeto/modo")
    p.add_argument("--cenario", choices=["reuniao", "sessoes"], default="reuniao",
                   help="reuniao: uma reunião por projeto; sessoes: várias reuniões com repetições e mudanças injetadas")
    p.add_argument("--sessoes", type=int, default=3, help="em quantas reuniões dividir (cenário sessoes)")
    p.add_argument("--sem-qualidade", action="store_true", help="não consultar o especialista de qualidade (ablação)")
    p.add_argument("--simulado", action="store_true", help="baseline por regras, sem API")
    p.add_argument("--ver-agentes", action="store_true")
    p.add_argument("--paralelo", type=int, default=4, help="quantas execuções ao mesmo tempo (padrão: 4)")
    p.add_argument("--entradas", default=entradas.PASTA_PADRAO)
    p.set_defaults(func=cmd_avaliar)

    p = sub.add_parser("anotacao", help="anotação manual das ligações (exportar amostra / calcular kappa)")
    p.add_argument("acao", choices=["exportar", "kappa"])
    p.add_argument("arquivo", help="CSV de saída (exportar) ou o CSV anotado (kappa)")
    p.add_argument("--amostra", type=int, default=60)
    p.add_argument("--provedor", default="deepseek-chat")
    p.add_argument("--cenario", default="*")
    p.set_defaults(func=cmd_anotacao)

    args = parser.parse_args()
    try:
        args.func(args)
    except ErroProvedor as exc:
        sys.exit(f"Erro: {exc}")


if __name__ == "__main__":
    main()
