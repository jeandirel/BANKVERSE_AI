"""BANKVERSE command-line interface (operations, pipelines, training, demo)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bankverse.core.config import get_settings
from bankverse.core.logging import configure_logging

BACKEND_DIR = Path(__file__).resolve().parents[2]
COMMANDS: dict[str, tuple[str, Callable[[argparse.Namespace], Any], Callable[[argparse.ArgumentParser], None]]] = {}


def command(name: str, help_: str, args: Callable[[argparse.ArgumentParser], None] = lambda p: None):
    def deco(fn: Callable[[argparse.Namespace], Any]) -> Callable[[argparse.Namespace], Any]:
        COMMANDS[name] = (help_, fn, args)
        return fn

    return deco


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


@command("db-upgrade", "Apply control-plane and tenant migrations",
         lambda p: p.add_argument("--db-url", default=None, help="Dedicated tenant database URL (tenant stream only)"))
def _db_upgrade(a: argparse.Namespace) -> None:
    base = [sys.executable, "-m", "alembic", "-c", str(BACKEND_DIR / "alembic.ini")]
    if a.db_url:
        subprocess.run([*base, "-n", "tenant", "-x", f"db_url={a.db_url}", "upgrade", "head"], check=True)  # noqa: S603
        return
    subprocess.run([*base, "-n", "control", "upgrade", "head"], check=True)  # noqa: S603
    subprocess.run([*base, "-n", "tenant", "upgrade", "head"], check=True)  # noqa: S603


@command("seed", "Create platform/DEMO/test organisations and users (credentials → data/.demo_credentials.json)")
def _seed(_: argparse.Namespace) -> None:
    from bankverse.demo.seed import seed

    _print(seed())


@command("data-register-sources", "Register/refresh the data sources registry in the database")
def _register_sources(_: argparse.Namespace) -> None:
    from bankverse.data.registry import sync_registry

    _print(sync_registry())


@command("data-ingest", "Download & verify external sources into the raw zone",
         lambda p: p.add_argument("sources", nargs="*", help="source keys (default: all reachable)"))
def _ingest(a: argparse.Namespace) -> None:
    from bankverse.data.ingest import ingest_sources

    _print(ingest_sources(a.sources or None))


@command("pipeline", "Run a data pipeline (bronze→silver→gold)",
         lambda p: (p.add_argument("name"), p.add_argument("--force", action="store_true")))
def _pipeline(a: argparse.Namespace) -> None:
    from bankverse.data.pipelines import run_pipeline

    _print(run_pipeline(a.name, force=a.force))


@command("train", "Train, evaluate and register a model family",
         lambda p: (p.add_argument("family"), p.add_argument("--trained-by", default="pipeline"),
                    p.add_argument("--quick", action="store_true", help="smaller budget (CI)")))
def _train(a: argparse.Namespace) -> None:
    from bankverse.ml.training import train_family

    _print(train_family(a.family, trained_by=a.trained_by, quick=a.quick))


@command("models", "List / approve / deploy / rollback models",
         lambda p: (p.add_argument("action", choices=["list", "approve", "reject", "deploy", "rollback"]),
                    p.add_argument("--name"), p.add_argument("--version", type=int),
                    p.add_argument("--reviewer", default=None), p.add_argument("--comment", default="")))
def _models(a: argparse.Namespace) -> None:
    from bankverse.ml.cli_ops import models_cli

    _print(models_cli(a))


@command("demo-generate", "Generate the synthetic DEMO bank (customers, accounts, cards, transactions, cases)",
         lambda p: (p.add_argument("--org", default="demo-bank"), p.add_argument("--seed", type=int, default=2026),
                    p.add_argument("--customers", type=int, default=1500), p.add_argument("--days", type=int, default=120)))
def _demo_generate(a: argparse.Namespace) -> None:
    from bankverse.demo.generator import generate_demo_bank

    _print(generate_demo_bank(org_slug=a.org, seed=a.seed, n_customers=a.customers, days=a.days))


@command("demo-score", "Run the production models over the DEMO bank (fraud, AML, credit, customer)",
         lambda p: p.add_argument("--org", default="demo-bank"))
def _demo_score(a: argparse.Namespace) -> None:
    from bankverse.demo.scoring import score_demo_bank

    _print(score_demo_bank(a.org))


@command("copilot-index", "Embed the public report corpus with the deployed copilot retriever")
def _copilot_index(_: argparse.Namespace) -> None:
    from bankverse.modules.copilot.service import index_public

    _print(index_public())


@command("demo-content", "Add DEMO document specimens and the DEMO bank knowledge base (needs deployed models)",
         lambda p: p.add_argument("--org", default="demo-bank"))
def _demo_content(a: argparse.Namespace) -> None:
    from bankverse.demo.content import seed_content

    _print(seed_content(a.org))


@command("serve", "Run the API server", lambda p: (p.add_argument("--port", type=int, default=8000),
                                                    p.add_argument("--host", default="127.0.0.1")))
def _serve(a: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("bankverse.api.app:app", host=a.host, port=a.port, proxy_headers=True, forwarded_allow_ips="*")


def main(argv: list[str] | None = None) -> None:
    s = get_settings()
    configure_logging(s.log_level, json=False)
    parser = argparse.ArgumentParser(prog="bankverse", description="BANKVERSE AI operations CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, (help_, _, add_args) in COMMANDS.items():
        add_args(sub.add_parser(name, help=help_))
    a = parser.parse_args(argv)
    COMMANDS[a.cmd][1](a)


if __name__ == "__main__":
    main()
