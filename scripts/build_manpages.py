"""Generate man pages from pace's Typer CLI tree.

Run with `uv run python scripts/build_manpages.py`.
Pages are written to `docs/man/`.
"""

from pathlib import Path

import typer.main
from click_man.core import write_man_pages

from pace import __version__
from pace.cli import app


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "docs" / "man"
    out_dir.mkdir(parents=True, exist_ok=True)

    click_app = typer.main.get_command(app)
    write_man_pages(
        name="pace",
        cli=click_app,
        version=__version__,
        target_dir=str(out_dir),
    )
    print(f"Man pages written to {out_dir}")


if __name__ == "__main__":
    main()
