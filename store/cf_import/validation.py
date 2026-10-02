from __future__ import annotations

import ast
import collections
import operator
import re
from itertools import pairwise

from .contracts import InputNode, ProblemSpec

OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}


def expression(value: int | str | None, env: dict) -> int:
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("Missing/oversized validator expression")

    def walk(n):
        if isinstance(n, ast.Constant) and type(n.value) is int:
            return n.value
        if isinstance(n, ast.Name) and type(env.get(n.id)) is int:
            return env[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -walk(n.operand) if isinstance(n.op, ast.USub) else walk(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in OPERATORS:
            result = OPERATORS[type(n.op)](walk(n.left), walk(n.right))
            if abs(result) > 10**18:
                raise ValueError("Validator arithmetic exceeded range")
            return result
        raise ValueError(
            "Unsupported validator expression (only int names and + - * // %)"
        )

    try:
        return walk(ast.parse(value, mode="eval").body)
    except (SyntaxError, ZeroDivisionError, RecursionError) as exc:
        raise ValueError("Invalid validator expression") from exc


def validate_input(text: str, spec: ProblemSpec) -> list[str]:
    if not text.strip():
        raise ValueError("Empty input")
    warnings = list(spec.validation_warnings)
    if not spec.input_schema:
        return warnings
    tokens = iter(text.split())
    totals: dict[str, int] = collections.defaultdict(int)
    fields_seen: set[str] = set()

    def token():
        try:
            return next(tokens)
        except StopIteration:
            raise ValueError(
                "Input has fewer tokens than its declared structure"
            ) from None

    def length(node, env):
        value = expression(node.length, env)
        if not 0 <= value <= 8_000_000:
            raise ValueError(f"Invalid {node.name} length")
        return value

    def scalar(node: InputNode, env: dict):
        word = token()
        if node.kind == "int":
            if not re.fullmatch(r"-?[0-9]+", word):
                raise ValueError(f"{node.name} must be an integer")
            value = int(word)
            if (
                not expression(node.minimum, env)
                <= value
                <= expression(node.maximum, env)
            ):
                raise ValueError(f"{node.name} out of range")
        else:
            value = word
            if node.length is not None and len(word) != expression(node.length, env):
                raise ValueError(f"{node.name} has invalid string length")
            if node.minimum is not None and len(word) < expression(node.minimum, env):
                raise ValueError(f"{node.name} string too short")
            if node.maximum is not None and len(word) > expression(node.maximum, env):
                raise ValueError(f"{node.name} string too long")
            if node.alphabet is not None and any(c not in node.alphabet for c in word):
                raise ValueError(f"{node.name} contains invalid characters")
        return value

    def graph(node: InputNode, env: dict):
        n, m = expression(node.vertices, env), length(node, env)
        if not 1 <= n <= 8_000_000:
            raise ValueError("Invalid graph vertex count")
        start = expression(node.minimum, env) if node.minimum is not None else 1
        edges = set()
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        adj, indegree = collections.defaultdict(list), [0] * n
        for _ in range(m):
            u, v = int(token()) - start, int(token()) - start
            if not (0 <= u < n and 0 <= v < n):
                raise ValueError("Graph vertex out of range")
            edge = (u, v) if node.directed else (min(u, v), max(u, v))
            if node.simple and (u == v or edge in edges):
                raise ValueError("Graph is not simple")
            edges.add(edge)
            a, b = find(u), find(v)
            if node.tree and a == b:
                raise ValueError("Tree contains a cycle")
            parent[a] = b
            if node.dag:
                adj[u].append(v)
                indegree[v] += 1
        if node.tree and m != n - 1:
            raise ValueError("Tree edge count must equal n - 1")
        if (node.connected or node.tree) and len({find(v) for v in range(n)}) != 1:
            raise ValueError("Graph is disconnected")
        if node.dag:
            queue = collections.deque(v for v in range(n) if not indegree[v])
            visited = 0
            while queue:
                u = queue.popleft()
                visited += 1
                for v in adj[u]:
                    indegree[v] -= 1
                    if not indegree[v]:
                        queue.append(v)
            if visited != n:
                raise ValueError("Directed graph is not a DAG")

    def nodes(items, env):
        for node in items:
            fields_seen.add(node.name)
            if node.kind in {"int", "string"}:
                value = scalar(node, env)
                env[node.name] = value
                totals[node.name] += value if isinstance(value, int) else len(value)
            elif node.kind == "repeat":
                for _ in range(length(node, env)):
                    nodes(node.children, dict(env))
            elif node.kind == "array":
                values = [
                    scalar(node.children[0], env) for _ in range(length(node, env))
                ]
                if node.distinct and len(set(values)) != len(values):
                    raise ValueError(f"{node.name} contains duplicates")
                if node.permutation and (
                    set(values) != set(range(1, len(values) + 1))
                    or len(set(values)) != len(values)
                ):
                    raise ValueError(f"{node.name} is not a permutation of 1..n")
                if node.sorted and any(a > b for a, b in pairwise(values)):
                    raise ValueError(f"{node.name} is not sorted")
                env[node.name] = values
                totals[node.name] += len(values)
            elif node.kind == "graph":
                graph(node, env)

    nodes(spec.input_schema, {})
    if next(tokens, None) is not None:
        raise ValueError("Input has unexpected trailing tokens")
    for constraint in spec.total_constraints:
        if constraint.field not in fields_seen:
            raise ValueError(
                f"Total constraint refers to unknown field {constraint.field}"
            )
        if totals[constraint.field] > constraint.maximum:
            raise ValueError(f"Total {constraint.field} exceeds {constraint.maximum}")
    return warnings
