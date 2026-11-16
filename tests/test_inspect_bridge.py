"""SP04: in-process SDK requests route to Inspect's local mock provider."""

from pathlib import Path

from inspect_ai import Task, eval
from inspect_ai.agent import agent_bridge
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, ModelUsage, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver
from openai import AsyncOpenAI

from agent_testbed.offline import offline_execution


@solver
def bridged_local_calls() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        async with agent_bridge(state) as bridge:
            async with AsyncOpenAI(api_key="local-fixture-not-a-secret", max_retries=0) as client:
                messages = []
                for role in ("planner", "worker", "verifier"):
                    messages.append({"role": "user", "content": role})
                    response = await client.chat.completions.create(
                        model="inspect", messages=messages
                    )
                    messages.append(
                        {"role": "assistant", "content": response.choices[0].message.content}
                    )
            return bridge.state

    return solve


def test_sdk_bridge_uses_only_mock_provider(tmp_path: Path) -> None:
    with offline_execution():
        logs = eval(
            Task(dataset=[Sample(input="bridge fixture")], solver=bridged_local_calls()),
            model=get_model(
                "mockllm/model",
                custom_outputs=[
                    ModelOutput.from_content("mockllm/model", text).model_copy(
                        update={
                            "usage": ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2)
                        }
                    )
                    for text in ("planned", "worked", "verified")
                ],
                memoize=False,
            ),
            score=False,
            log_dir=str(tmp_path),
            display="none",
            ctl_server=False,
            acp_server=False,
            notification=False,
            sentinel_proxy="off",
            log_realtime=False,
        )
    assert logs[0].status == "success", logs[0].error
    assert logs[0].samples[0].output.completion == "verified"
    events = [event for event in logs[0].samples[0].events if event.event == "model"]
    assert len(events) == 3
    assert all(event.model.startswith("mockllm/") for event in events)
    # These are simulated mock-provider tokens, not real provider usage or cost.
