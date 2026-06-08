"""Pylint plugin for serialx."""

from __future__ import annotations

from astroid.nodes import Arguments, AssignName, FunctionDef
from pylint.checkers import BaseChecker
from pylint.lint import PyLinter


class ParamReassignChecker(BaseChecker):
    """Flag rebinding a function parameter to a new value."""

    name = "serialx-param-reassign"
    msgs = {
        # serialx reserves pylint message base 90: codes are {C,W,E,R}90xx.
        "W9001": (
            "Reassigning function parameter %r; bind a new local instead",
            "serialx-reassigned-parameter",
            "Rebinding a parameter overloads one name with two meanings and lets "
            "a rewritten value leak into later uses of the original.",
        ),
    }

    def visit_assignname(self, node: AssignName) -> None:
        """Flag an assignment target that rebinds an enclosing function parameter."""
        # Skip the parameter *definition* itself (its target lives under Arguments).
        if isinstance(node.parent, Arguments):
            return

        scope = node.scope()
        if not isinstance(scope, FunctionDef):
            return

        args = scope.args
        names: set[str] = set()
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            names.update(arg.name for arg in group or [])
        if args.vararg:
            names.add(args.vararg)
        if args.kwarg:
            names.add(args.kwarg)

        if node.name in names:
            self.add_message(
                "serialx-reassigned-parameter", node=node, args=(node.name,)
            )


def register(linter: PyLinter) -> None:
    """Register the serialx checkers with pylint."""
    linter.register_checker(ParamReassignChecker(linter))
