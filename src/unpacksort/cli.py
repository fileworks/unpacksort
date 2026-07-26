"""Typer command and stable process outcomes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from unpacksort import __version__
from unpacksort.engine import Processor
from unpacksort.journal import StateConflictError
from unpacksort.mail import is_confirmed_mbox
from unpacksort.models import ExitOutcome
from unpacksort.policy import GIB, Policy
from unpacksort.safety import paths_alias

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Safely recover and deterministically organize mail and archive content.",
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"unpacksort {__version__}")
        raise typer.Exit


@app.command()
def main(
    source: Annotated[
        Path,
        typer.Argument(help="A regular mbox file or directory tree."),
    ],
    destination: Annotated[
        Path,
        typer.Argument(help="Destination directory for grouped output and journal."),
    ],
    *,
    flatten: Annotated[
        bool,
        typer.Option("--flatten", help="Publish directly beneath each type group."),
    ] = False,
    pdf_only: Annotated[
        bool,
        typer.Option("--pdf-only", help="Publish only parser-valid unencrypted PDFs."),
    ] = False,
    max_depth: Annotated[
        int,
        typer.Option(min=1, help="Maximum combined mail/archive recursion depth."),
    ] = 10,
    max_members_per_container: Annotated[
        int,
        typer.Option(min=1, help="Maximum logical members in one container."),
    ] = 100_000,
    max_members_run: Annotated[
        int,
        typer.Option(min=1, help="Maximum logical members in the run."),
    ] = 1_000_000,
    max_member_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum expanded bytes for one member."),
    ] = 2 * GIB,
    max_container_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum expanded bytes for one container subtree."),
    ] = 20 * GIB,
    max_run_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum logical expanded bytes in the run."),
    ] = 100 * GIB,
    max_expansion_ratio: Annotated[
        float,
        typer.Option(min=0.001, help="Maximum declared or observed expansion ratio."),
    ] = 1_000.0,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    """Recover content into deterministic type groups."""

    del version
    try:
        policy = Policy(
            layout="flatten" if flatten else "hierarchy",
            pdf_only=pdf_only,
            max_depth=max_depth,
            max_members_per_container=max_members_per_container,
            max_members_run=max_members_run,
            max_member_bytes=max_member_bytes,
            max_container_bytes=max_container_bytes,
            max_run_bytes=max_run_bytes,
            max_expansion_ratio=max_expansion_ratio,
        )
        policy.validate()
        _validate_paths(source, destination)
    except (OSError, ValueError) as error:
        typer.echo(f"input error: {error}", err=True)
        raise typer.Exit(ExitOutcome.USAGE) from error
    try:
        manifest, report, outcome = Processor(source, destination, policy).run()
    except StateConflictError as error:
        typer.echo(f"state conflict: {error}", err=True)
        raise typer.Exit(ExitOutcome.CONFLICT) from error
    except KeyboardInterrupt as error:
        typer.echo("interrupted; committed journal work can be resumed", err=True)
        raise typer.Exit(ExitOutcome.INTERRUPTED) from error
    except Exception as error:
        typer.echo(f"fatal error: {error}", err=True)
        raise typer.Exit(ExitOutcome.FATAL) from error
    typer.echo(f"destination={destination.resolve()}")
    typer.echo(f"manifest={manifest.resolve()}")
    typer.echo(f"report={report.resolve()}")
    typer.echo(f"outcome={'partial' if outcome is ExitOutcome.PARTIAL else 'complete'}")
    if outcome is ExitOutcome.PARTIAL:
        typer.echo(f"partial success; inspect stable reasons in {report}", err=True)
    raise typer.Exit(outcome)


def _validate_paths(source: Path, destination: Path) -> None:
    if not source.exists():
        msg = f"source does not exist: {source}"
        raise ValueError(msg)
    if source.is_symlink():
        msg = "source cannot be a symlink"
        raise ValueError(msg)
    if not source.is_dir() and not source.is_file():
        msg = "source must be a regular mbox file or directory"
        raise ValueError(msg)
    if source.is_file() and not is_confirmed_mbox(source):
        msg = "a regular-file source must be a parser-confirmed mbox"
        raise ValueError(msg)
    if destination.exists() and not destination.is_dir():
        msg = "destination exists and is not a directory"
        raise ValueError(msg)
    if paths_alias(source, destination):
        msg = "source and destination alias each other"
        raise ValueError(msg)
    source_resolved = source.resolve()
    destination_resolved = destination.resolve()
    if source.is_dir() and source_resolved in destination_resolved.parents:
        msg = "destination cannot be inside the source tree"
        raise ValueError(msg)
    if destination.exists() and destination_resolved in source_resolved.parents:
        msg = "source cannot be inside the destination"
        raise ValueError(msg)


def run() -> None:
    """Console-script wrapper."""

    app()


if __name__ == "__main__":
    run()
