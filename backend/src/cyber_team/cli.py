"""Cyber-Team CLI entry point."""

import click
import subprocess
import os
import sys


@click.group()
def main():
    """Cyber-Team — AI-powered digital company operating system."""
    pass


@main.command()
@click.option("--build", is_flag=True, help="Build containers before starting")
@click.option("--detach", is_flag=True, help="Run in background")
def start(build, detach):
    """Start the Cyber-Team stack."""
    cmd = ["docker", "compose", "up"]
    if build:
        cmd.append("--build")
    if detach:
        cmd.append("-d")
    click.echo("🚀 Starting Cyber-Team stack...")
    result = subprocess.run(cmd, cwd=_project_root())
    sys.exit(result.returncode)


@main.command()
def stop():
    """Stop the Cyber-Team stack."""
    click.echo("Stopping Cyber-Team stack...")
    subprocess.run(["docker", "compose", "down"], cwd=_project_root())


@main.command()
@click.argument("service")
def logs(service):
    """View logs for a specific service."""
    subprocess.run(["docker", "compose", "logs", "-f", service], cwd=_project_root())


@main.command()
def status():
    """Show status of all services."""
    subprocess.run(["docker", "compose", "ps"], cwd=_project_root())


@main.command()
@click.option("--reset-db", is_flag=True, help="Reset the database")
def setup(reset_db):
    """Initial setup wizard."""
    env_file = os.path.join(_project_root(), ".env")
    if not os.path.exists(env_file):
        click.echo("Creating .env from .env.example...")
        example = os.path.join(_project_root(), ".env.example")
        if os.path.exists(example):
            subprocess.run(["cp", example, env_file])
            click.echo(f"Edit {env_file} with your API keys before starting.")
        else:
            click.echo("No .env.example found. Create .env manually.")
    else:
        click.echo(".env already exists.")

    if reset_db:
        click.echo("Resetting database...")
        subprocess.run(["docker", "compose", "down", "-v"], cwd=_project_root())

    click.echo("Setup complete. Run 'cyber-team start --build' to launch.")


@main.command()
def screen_start():
    """Start Cyber-Team in a screen session (screen -r cyber-team)."""
    root = _project_root()
    cmd = f"screen -dmS cyber-team bash -c 'docker compose up --build 2>&1 | tee /tmp/cyber-team.log'"
    click.echo("Starting Cyber-Team in screen session 'cyber-team'...")
    click.echo("Attach with: screen -r cyber-team")
    os.system(cmd)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


if __name__ == "__main__":
    main()
