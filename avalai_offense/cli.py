import argparse
import json
import sys
from pathlib import Path
from .common import dumps
from .config import connection, env_values, load_config
from .inputs import read_records


def main(argv=None):
    parser = argparse.ArgumentParser(description="Four independent AvalAI offense judges (Persian / English)")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "dry-run"):
        p = sub.add_parser(command)
        p.add_argument("--env", default=".env")
        p.add_argument("--input", required=True)
        p.add_argument("--text-column", default="text")
        p.add_argument("--id-column", default="id")
        p.add_argument("--context-column", default="context")
        p.add_argument("--language-column", default="language")
        p.add_argument("--source-column", default="source")
        p.add_argument("--limit", type=int)
        if command == "run":
            p.add_argument("--output", required=True)
            p.add_argument("--retry-failed", action="store_true", help="Retry cached failed votes; successful votes remain cached")
    p = sub.add_parser("models")
    p.add_argument("--env", default=".env")
    p.add_argument("--public", action="store_true")
    p = sub.add_parser("export")
    p.add_argument("--run-dir", required=True)
    p = sub.add_parser("review")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--workbook", required=True)
    p = sub.add_parser("demo")
    p.add_argument("--output", default="outputs/demo")
    args = parser.parse_args(argv)
    try:
        if args.command in ("run", "dry-run"):
            cfg = load_config(args.env, require_key=args.command == "run")
            records = read_records(args.input, args.text_column, args.id_column, args.context_column,
                                   args.language_column, args.source_column, args.limit)
            if args.command == "dry-run":
                print(dumps({"records": len(records), "unique_requests_per_model": len({r["content_hash"] for r in records}),
                             "maximum_first_pass_calls": 4 * len({r["content_hash"] for r in records}),
                             "models": [m.id for m in cfg.models], "network_calls": 0}, indent=2))
            else:
                from .pipeline import run_pipeline
                summary, stopped = run_pipeline(records, cfg, args.output, retry_failed=args.retry_failed)
                print(dumps(summary, indent=2))
                if stopped:
                    return 2
        elif args.command == "models":
            from .provider import fetch_json, make_opener
            values = env_values(args.env)
            key, base = connection(values)
            opener = make_opener(values.get("NETWORK_MODE", "direct").strip().lower(),
                                 values.get("PROXY_URL", "").strip(),
                                 values.get("BIND_IP", "").strip())
            if args.public or not key:
                data = fetch_json("https://api.avalai.ir/public/models", opener=opener)
            else:
                data = fetch_json(base + "/models", key, opener=opener)
            models = data.get("data", []) if isinstance(data, dict) else data
            for m in models:
                print(m.get("id", ""), m.get("owned_by", ""), sep="\t")
        elif args.command == "export":
            from .pipeline import export_existing
            print(dumps(export_existing(args.run_dir), indent=2))
        elif args.command == "review":
            from .export import import_reviews
            print(f"Imported {import_reviews(args.run_dir, args.workbook)} human decisions. Outputs regenerated.")
        else:
            from .demo import run_demo
            print(dumps(run_demo(args.output), indent=2))
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Successful votes remain in state.sqlite; rerun to resume.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
