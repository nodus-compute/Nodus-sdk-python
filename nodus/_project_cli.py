"""Source selection shared by sandbox and managed agent commands."""

import argparse
import re

from ._projects import setup_command
from .errors import ValidationError


def _repository(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value) or ".." in value or value.endswith(".git"):
        raise argparse.ArgumentTypeError("Use a connected GitHub repository as owner/name without a URL or credentials")
    return value


def _ref(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", value) or ".." in value or "//" in value or value.endswith(("/", ".lock")):
        raise argparse.ArgumentTypeError("Use a GitHub branch or refs/tags/name without dot segments")
    return value


def add_source_arguments(parser, *, project_help):
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--project", help=project_help)
    source.add_argument("--source-asset-id", help="reuse an immutable uploaded project")
    source.add_argument("--github-repo", type=_repository, metavar="OWNER/REPO", help="use a repository accessible through your connected GitHub account")
    parser.add_argument("--github-ref", type=_ref, metavar="REF", help="repository branch, tag ref or commit, requires --github-repo")


def source_options(args, *, default_project=False):
    if args.github_ref is not None and args.github_repo is None:
        raise ValidationError("--github-ref requires --github-repo")
    options = {"project": args.project, "source_asset_id": args.source_asset_id,
               "setup": args.setup, "network_permissions": args.network_permission}
    if args.github_repo is not None:
        bootstrap = {"repo": args.github_repo}
        if args.github_ref is not None:
            bootstrap["ref"] = args.github_ref
        if args.setup is not None:
            bootstrap["setup"] = setup_command(args.setup)
        permissions = list(args.network_permission or [])
        if "github" not in permissions:
            permissions.append("github")
        options.update(bootstrap=bootstrap, setup=None, network_permissions=permissions)
    elif default_project and args.source_asset_id is None:
        options["project"] = args.project or "."
    return options
