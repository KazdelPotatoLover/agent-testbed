"""Trusted deterministic fixtures, not general agents or security sandboxes."""

import csv
import io
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from agent_testbed.contracts import TaskSpec
from agent_testbed.io import TestbedError, contained_path, read_bytes, write_new


def _contract(task: TaskSpec, inputs: Path) -> dict[str, Any]:
    text = read_bytes(contained_path(inputs, task.inputs[0].path)).decode("utf-8")
    values: dict[str, Any] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\[(P\d+)\] ([a-z_]+): (.+)", line)
        if match:
            paragraph, field, value = match.groups()
            values[field] = {"value": value, "evidence": paragraph}
    return values


def _orders(task: TaskSpec, inputs: Path) -> str:
    datasets = []
    for asset in task.inputs:
        rows = list(
            csv.DictReader(io.StringIO(read_bytes(contained_path(inputs, asset.path)).decode()))
        )
        datasets.append(rows)
    left, right = datasets
    counts = Counter(row["order_id"] for row in left + right)
    left_index = {row["order_id"]: row["amount"] for row in left}
    right_index = {row["order_id"]: row["amount"] for row in right}
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["order_id", "issue", "left_amount", "right_amount"])
    for order_id in sorted(left_index.keys() | right_index.keys()):
        a, b = left_index.get(order_id, ""), right_index.get(order_id, "")
        expected_count = int(order_id in left_index) + int(order_id in right_index)
        if counts[order_id] > expected_count:
            issue = "duplicate"
        elif not a:
            issue = "missing_left"
        elif not b:
            issue = "missing_right"
        elif Decimal(a) != Decimal(b):
            issue = "amount_mismatch"
        else:
            continue
        writer.writerow([order_id, issue, a, b])
    return output.getvalue()


def execute_mock(task: TaskSpec, system_id: str, inputs: Path, output: Path) -> str:
    if system_id not in {"mock-pass", "mock-wrong-answer"}:
        raise TestbedError("Only allowlisted deterministic mock systems are supported")
    if len(task.outputs) != 1:
        raise TestbedError("M1 fixture drivers each support one output")
    if task.fixture_driver == "extract_contract":
        answer = _contract(task, inputs)
        if system_id == "mock-wrong-answer":
            answer.pop(next(iter(answer)), None)
        content = json.dumps(answer, ensure_ascii=False, sort_keys=True) + "\n"
    else:
        content = _orders(task, inputs)
        if system_id == "mock-wrong-answer":
            content = content.splitlines()[0] + "\n"
    write_new(contained_path(output, task.outputs[0].path), content.encode())
    # Both claim success on purpose: independent scoring must detect the wrong system.
    return json.dumps({"success": True, "artifacts": [task.outputs[0].path]})
