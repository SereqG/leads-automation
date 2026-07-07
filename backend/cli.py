import sys
from typing import Any

import typer

from apps.prospects.cli import app as prospects_app

app = typer.Typer(help="LeadGen Platform CLI")
app.add_typer(prospects_app, name="prospects")


def _resolve_missing_command(command: Any) -> list[str]:
    """Walk down the command tree, prompting for a subcommand at each
    group that wasn't given one, until a runnable command is reached."""
    path: list[str] = []
    while getattr(command, "commands", None):
        names = sorted(command.commands)
        if len(names) == 1:
            choice = names[0]
        else:
            choice = typer.prompt(f"Which command? ({', '.join(names)})")
        path.append(choice)
        command = command.commands[choice]
    return path


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.extend(_resolve_missing_command(typer.main.get_command(app)))
    app()
