"""Regression (sysv-d77 VM): the CRUX and FreeBSD lives run Python 3.12, which
evaluates annotations when a module is imported; the development host's 3.14
does not. An annotation naming something defined later in the module passes
here and fails there, so module-level annotations are checked statically."""

from pathlib import Path
import ast
import builtins
import unittest

ROOT = Path(__file__).resolve().parent.parent


def forward_references(path: Path) -> list:
    tree = ast.parse(path.read_text())
    defined = set(dir(builtins))
    found = []

    def names(node):
        if node is None or isinstance(node, ast.Constant):  # string annotations stay lazy
            return []
        return [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]

    def check(fn, owner=None):
        args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        for n in sum((names(a.annotation) for a in args), names(fn.returns)):
            if n not in defined and n != owner:
                found.append(f"{path.relative_to(ROOT)}:{fn.lineno} {fn.name}: {n}")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check(sub, node.name)
                elif isinstance(sub, ast.AnnAssign):
                    found += [f"{path.relative_to(ROOT)}:{sub.lineno} {node.name}: {n}"
                              for n in names(sub.annotation) if n not in defined and n != node.name]
        for n in ast.walk(node) if isinstance(node, (ast.Import, ast.ImportFrom, ast.Try, ast.If)) else []:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                defined |= {(a.asname or a.name).split(".")[0] for a in n.names}
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                defined |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
    return found


class TestImportPortability(unittest.TestCase):
    def test_no_forward_references_in_evaluated_annotations(self) -> None:
        problems = [p for d in ("mocinha", "tools/qemu") for f in sorted((ROOT / d).rglob("*.py"))
                    for p in forward_references(f)]
        self.assertEqual(problems, [])

    def test_check_catches_the_sysvd77_failure(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            f = Path(tmp) / "m.py"
            f.write_text("from typing import List\ndef f() -> List[Later]: ...\nclass Later: ...\n")
            self.assertEqual(len(forward_references(f)), 1)


if __name__ == "__main__":
    unittest.main()
