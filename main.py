"""GigaBrain command line.

Commands:
    python main.py chat                       talk to the Digital Twin (DeepSeek)
    python main.py chat --simulated           same, without calling an API (rule-based answers)
    python main.py demo                       runs sample conversations into data-demo/
    python main.py dashboard                  visual dashboard of every data folder
    python main.py api                        REST API for the web application (docs at /docs)
    python main.py requirements               list the requirements
    python main.py trace R3                   show how R3 came to be
    python main.py experts                    list the experts
    python main.py feed payments minutes.md   give a project document to an expert
    python main.py dataset                    list the projects of the test dataset
    python main.py evaluate 2008-keepass      process the meeting and compare with the gold standard
    python main.py evaluate all --repetitions 5      every project, 5 times, with statistics
    python main.py evaluate all --scenario sessions  several meetings with injected repetitions and changes
    python main.py annotation export links.csv       sample of links for manual annotation
    python main.py annotation kappa links.csv        agreement between the two annotators

Global option --data <folder> chooses where the databases live (default: data/).

To use DeepSeek:  $env:DEEPSEEK_API_KEY="your-key"  (PowerShell)  or  export DEEPSEEK_API_KEY=your-key
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from gigabrain import annotation, dataset, demo, meeting, requirement_format, scenarios, stats
from gigabrain.council import Council, feed_expert, open_context
from gigabrain.providers import ProviderError, SimulatedProvider, create_provider

ROOT = os.path.dirname(os.path.abspath(__file__))


def _provider(simulated: bool):
    return SimulatedProvider() if simulated else create_provider("deepseek")


def _print_requirement(req: dict) -> None:
    print(f"\n  ## {req.get('title')}  ({req.get('class')}{'/' + req['subtype'] if req.get('subtype') else ''}, "
          f"topics: {', '.join(req.get('topics', []))})")
    print(f"  Statement: {req.get('statement')}")
    if req.get("story"):
        print(f"  Story: {requirement_format.story_text(req['story'])}")
    for c in req.get("acceptance_criteria", []):
        print(f"   - {c.get('scenario')}: Given {c.get('given')} / When {c.get('when')} / Then {c.get('then')}")
    if req.get("base"):
        print(f"  -> new version of {req['base']}")
    for link in req.get("links", []):
        print(f"  -> {link['kind']} {link['target']}: {link.get('reason', '')}")


def cmd_chat(args) -> None:
    provider = _provider(args.simulated)
    ctx = open_context(args.data, provider, mode=args.mode, show_agents=not args.quiet)
    council = Council(ctx)
    conversation = council.start()

    print(f"=== GigaBrain · conversation {conversation} · mode {args.mode} · provider {provider.name} ===")
    print("Describe what you need. Reply 'approved' when the proposal is good. Type 'exit' to leave without approving.\n")
    while True:
        try:
            text = input("PO> ").strip()
        except (EOFError, KeyboardInterrupt):
            text = "exit"
        if not text:
            continue
        if text.lower() in ("exit", "quit"):
            council.end()
            print("Conversation closed without approval.")
            break
        try:
            answer = council.talk(text)
        except Exception as exc:  # an API failure does not end the conversation
            print(f"\n[error: {exc}]\n")
            continue
        print(f"\nTwin> {answer['message_to_po']}")
        for req in answer.get("requirements", []):
            _print_requirement(req)
        print()
        if answer["action"] == "approved":
            break
    ctx.db.close()


def cmd_demo(args) -> None:
    folder = args.data if args.data != "data" else os.path.join(ROOT, "data-demo")
    if os.path.exists(folder):
        shutil.rmtree(folder)
    demo.run(folder)
    print("\nDone. See it in the dashboard: python main.py dashboard")


def cmd_dashboard(args) -> None:
    from gigabrain.dashboard.server import serve
    # Without --data, the dashboard shows every data folder of the project (with a selector).
    serve(args.data if args.data != "data" else ROOT, args.port, open_browser=not args.no_browser)


def cmd_requirements(args) -> None:
    ctx = open_context(args.data, SimulatedProvider())
    for req in ctx.db.list_requirements():
        print(f"{req['id']:>4}  v{req['current_version']}  {req['status']:<12} {req['content'].get('statement') or req['title']}  [{', '.join(req['topics'])}]")
    for link in ctx.db.list_links():
        print(f"      {link['source_id']} {link['kind']} {link['target_id']}")


def cmd_trace(args) -> None:
    ctx = open_context(args.data, SimulatedProvider())
    for step in ctx.db.trace(args.requirement):
        if step["event"] == "version":
            print(f"{step['timestamp']}  {step['requirement_id']} v{step['version']}  {step['title']}  ({step['reason']})")
        else:
            print(f"{step['timestamp']}  {step['requirement_id']} {step['kind']} {step['target_id']}  ({step['reason']})")


def cmd_experts(args) -> None:
    ctx = open_context(args.data, SimulatedProvider())
    for exp in ctx.db.list_experts():
        print(f"{exp['id']:<16} v{exp['knowledge_version']}  {exp['consultations']} consultation(s)  {exp['file']}")


def cmd_feed(args) -> None:
    ctx = open_context(args.data, _provider(args.simulated), show_agents=True)
    with open(args.file, encoding="utf-8") as f:
        version = feed_expert(ctx, args.expert, os.path.basename(args.file), f.read())
    print(f"Knowledge of {args.expert}: " + (f"version {version}" if version else "no changes"))


def cmd_dataset(args) -> None:
    for p in dataset.list_projects(args.dataset):
        print(f"{p['folder']:<22} tier {p['tier']}  {p['requirements']:>3} requirements  {p['words']:>5} words  {p['name']}")


def _fmt(v) -> str:
    return "  -  " if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)


COLUMNS = ["predicted", "gold", "hits", "precision", "recall", "f1", "class", "subtype", "trace", "unsupported",
           "ears", "story", "gherkin", "not_vague"]
SESSION_COLUMNS = ["repetitions_avoided", "changes_captured", "changes_linked"]


def _run_evaluation(args, project_name: str, mode: str, rep: int, root: str) -> dict:
    """One run (project + mode + repetition), with its own folder, databases and provider."""
    project = dataset.load_project(project_name, args.dataset)
    folder = os.path.join(root, args.scenario, project_name, mode, f"rep-{rep}")
    if os.path.exists(folder):
        shutil.rmtree(folder)
    ctx = open_context(folder, _provider(args.simulated), show_agents=args.show_agents)
    ctx.with_quality = not args.no_quality
    try:
        if args.scenario == "sessions":
            scenario = scenarios.generate_sessions(project, n_sessions=args.sessions)
            report = meeting.process_sessions_and_evaluate(ctx, project, scenario, folder, mode)
        else:
            report = meeting.process_and_evaluate(ctx, project, folder, mode)
    finally:
        ctx.db.close()
    return {"project": project_name, "mode": mode, "rep": rep, **report["metrics"]}


def _row(label: str, mode: str, values: dict, columns: list[str]) -> str:
    return f"{label:<22} {mode:<13} " + " ".join(f"{_fmt(values.get(c)):>9}" for c in columns)


def cmd_evaluate(args) -> None:
    provider_name = SimulatedProvider.name if args.simulated else create_provider("deepseek").name
    projects = [p["folder"] for p in dataset.list_projects(args.dataset) if p["requirements"] > 0]
    if args.project != "all":
        projects = [args.project]
    modes = list(meeting.MODES) if args.mode == "all" else args.mode.split(",")
    root = os.path.join(ROOT, "data-eval", provider_name)

    # Each (project, mode, repetition) is independent: several run at the same time.
    tasks = [(name, mode, rep) for rep in range(1, args.repetitions + 1) for name in projects for mode in modes]
    columns = COLUMNS + (SESSION_COLUMNS if args.scenario == "sessions" else [])
    rows = []
    print(f"{len(tasks)} run(s) · scenario {args.scenario} · {args.parallel} at a time")
    print(f"{'project':<22} {'mode':<13} " + " ".join(f"{c[:9]:>9}" for c in columns))
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {executor.submit(_run_evaluation, args, *t, root): t for t in tasks}
        for future in as_completed(futures):
            name, mode, rep = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # one failed run does not stop the others
                print(f"{name:<22} {mode:<13} rep {rep} ERROR: {exc}")
                continue
            rows.append(row)
            print(_row(f"{name} #{rep}", mode, row, columns), flush=True)
    if not rows:
        return
    rows.sort(key=lambda r: (r["project"], meeting.MODES.index(r["mode"]), r["rep"]))

    summary = stats.summarize(rows)
    print()
    for mode, ms in summary["per_mode"].items():
        print(_row("MEAN", mode, {c: v["mean"] for c, v in ms.items()}, columns))
    if args.repetitions > 1:
        for mode, ms in summary["per_mode"].items():
            print(_row("± between repetitions", mode, {c: v["stdev_between_repetitions"] for c, v in ms.items()}, columns))
    if summary["comparisons"]:
        print("\nPaired comparison by project (Wilcoxon; rank-biserial effect, >0 favors the first mode):")
        for c in summary["comparisons"]:
            mark = " *" if c["p"] < 0.05 else ""
            print(f"  {c['metric']:<20} {c['a']} {c['mean_a']:.3f} x {c['b']} {c['mean_b']:.3f}"
                  f"  p={'<0.0001' if c['p'] < 0.0001 else format(c['p'], '.4f')}  effect={c['effect']:+.2f}  ({c['projects']} projects){mark}")

    scenario_folder = os.path.join(root, args.scenario)
    os.makedirs(scenario_folder, exist_ok=True)
    with open(os.path.join(scenario_folder, "summary.csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["project", "mode", "rep", *columns], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with open(os.path.join(scenario_folder, "statistics.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\nSummary in {os.path.relpath(scenario_folder, ROOT)} (summary.csv and statistics.json). Dashboard: python main.py dashboard")


def cmd_annotation(args) -> None:
    if args.action == "export":
        n = annotation.export(ROOT, args.file, args.sample, provider=args.provider, scenario=args.scenario)
        print(f"{n} link(s) drawn into {args.file}. Each annotator fills in their column with c (correct) or i (incorrect).")
    else:
        r = annotation.kappa(args.file)
        if not r["annotated"]:
            sys.exit("No row has both annotation columns filled in.")
        print(f"{r['annotated']} links annotated by both · agreement {r['agreement']:.0%} · Cohen's kappa {r['kappa']:.2f}")
        print(f"link precision: annotator 1 {r['precision_annotator_1']:.0%} · annotator 2 {r['precision_annotator_2']:.0%}"
              f" · consensus (both say correct) {r['precision_consensus']:.0%}")


def cmd_api(args) -> None:
    import uvicorn
    from gigabrain.api import create_app

    if not args.simulated:
        create_provider("deepseek")  # fail early if the key is not set
    factory = SimulatedProvider if args.simulated else (lambda: create_provider("deepseek"))
    token = args.token or os.environ.get("GIGABRAIN_API_TOKEN")
    origins = [o.strip() for o in args.origins.split(",")] if args.origins else ["*"]
    app = create_app(args.data, factory, origins=origins, token=token)
    print(f"GigaBrain API at http://{args.host}:{args.port}  ·  docs: http://{args.host}:{args.port}/docs")
    print(f"data: {os.path.abspath(args.data)} · provider: {'simulated' if args.simulated else 'deepseek'}"
          f" · token: {'yes' if token else 'no'} · origins: {', '.join(origins)}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="GigaBrain: multi-agent council for software requirements")
    parser.add_argument("--data", default="data", help="folder of the databases, logs and knowledge files")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("chat", help="talk to the Digital Twin")
    p.add_argument("--simulated", action="store_true", help="no API call; rule-based answers")
    p.add_argument("--mode", choices=["after_meeting", "in_meeting"], default="after_meeting")
    p.add_argument("--quiet", action="store_true", help="hide the internal conversation of the agents")
    p.set_defaults(func=cmd_chat)

    sub.add_parser("demo", help="run sample conversations with the simulated provider").set_defaults(func=cmd_demo)

    p = sub.add_parser("dashboard", help="visual dashboard in the browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_dashboard)

    sub.add_parser("requirements", help="list the requirements").set_defaults(func=cmd_requirements)
    p = sub.add_parser("trace", help="how a requirement came to be")
    p.add_argument("requirement")
    p.set_defaults(func=cmd_trace)
    sub.add_parser("experts", help="list the experts").set_defaults(func=cmd_experts)

    p = sub.add_parser("feed", help="give a project document to an expert")
    p.add_argument("expert")
    p.add_argument("file")
    p.add_argument("--simulated", action="store_true")
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("dataset", help="list the projects of the test dataset")
    p.add_argument("--dataset", default=dataset.DEFAULT_FOLDER, help="folder of the EntradasGigabrain repository")
    p.set_defaults(func=cmd_dataset)

    p = sub.add_parser("evaluate", help="process dataset meetings and compare with the gold standard")
    p.add_argument("project", help="project folder (e.g. 2008-keepass) or 'all'")
    p.add_argument("--mode", default="all", help="all (default) or a comma-separated list: council,single_agent,plain_llm")
    p.add_argument("--repetitions", type=int, default=1, help="how many times to run each project/mode")
    p.add_argument("--scenario", choices=["meeting", "sessions"], default="meeting",
                   help="meeting: one meeting per project; sessions: several meetings with injected repetitions and changes")
    p.add_argument("--sessions", type=int, default=3, help="how many meetings to split into (sessions scenario)")
    p.add_argument("--no-quality", action="store_true", help="do not consult the quality expert (ablation)")
    p.add_argument("--simulated", action="store_true", help="rule-based baseline, no API")
    p.add_argument("--show-agents", action="store_true")
    p.add_argument("--parallel", type=int, default=4, help="how many runs at the same time (default: 4)")
    p.add_argument("--dataset", default=dataset.DEFAULT_FOLDER)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("annotation", help="manual annotation of the links (export a sample / compute kappa)")
    p.add_argument("action", choices=["export", "kappa"])
    p.add_argument("file", help="output CSV (export) or the annotated CSV (kappa)")
    p.add_argument("--sample", type=int, default=60)
    p.add_argument("--provider", default="deepseek-chat")
    p.add_argument("--scenario", default="*")
    p.set_defaults(func=cmd_annotation)

    p = sub.add_parser("api", help="REST API for the web application (PO conversation and history)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to accept connections from other machines")
    p.add_argument("--simulated", action="store_true", help="no API call; rule-based answers (to build the front end)")
    p.add_argument("--origins", default="", help="CORS origins, comma-separated (default: all)")
    p.add_argument("--token", default="", help="require Authorization: Bearer <token> (or set GIGABRAIN_API_TOKEN)")
    p.set_defaults(func=cmd_api)

    args = parser.parse_args()
    try:
        args.func(args)
    except ProviderError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
