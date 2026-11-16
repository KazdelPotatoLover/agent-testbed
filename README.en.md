# Agent Testbed

**Compare agent architectures on quality, cost, latency, and reliability—with the same experimental framework.**

[简体中文](<README.md>) | **English**

Agent Testbed is a unified evaluation framework for agent systems. It is designed to evaluate single-agent, planner–worker, shared-memory, blackboard-based, and autonomous multi-agent architectures under consistent tasks, tool capabilities, and resource constraints.

It brings task execution, resource accounting, independent grading, and result analysis into one traceable workflow, helping researchers and engineering teams answer a practical question: **Which agent architecture works best for which tasks, and at what cost?**

> **Project status: early development.** This README describes the product design and intended capabilities. Implementations, integration examples, and usage documentation will be added as development progresses.

## Why Agent Testbed?

The value of an agent architecture lies in what it delivers on real tasks. Additional collaborators, shared memory, and verification stages can improve quality, while also introducing more model calls, longer waits, and more complex failure paths.

Agent Testbed organizes experiments around these trade-offs:

- **Architecture selection**: compare single agents, centralized delegation, and autonomous collaboration on the same task suite.
- **Mechanism evaluation**: use ablation studies to investigate the contribution of shared memory, independent verification, and scheduling strategies.
- **Regression tracking**: measure how model, prompt, and implementation changes affect quality and resource consumption.
- **Task acceptance**: verify completion through structured answers, delivered files, and observable environment state.
- **Reliability analysis**: examine behavior during timeouts, cancellation, service failures, and recovery.

## Core Capabilities

| Capability | Design and value |
| --- | --- |
| **Unified system integration** | Connect implementations through Python, CLI, and HTTP adapters while preserving each system's own planning and collaboration logic. |
| **Configuration-driven experiments** | Combine task suites, system configurations, repetitions, and budgets into a run matrix, with versions and conditions recorded for each experiment. |
| **Independent outcome grading** | Evaluate answers, files, and environment changes with rules, hidden tests, rubrics, model-based review, or human review. |
| **Usage and budget management** | Account for model calls across the entire system, track tokens, costs, and usage completeness, and check budgets before calls in controlled integrations. |
| **Isolation and recovery** | Manage separate workspaces, memory boundaries, timeouts, cancellation, and cleanup; persist state and resume unfinished experiments. |
| **Traceable reporting** | Compare quality, cost, latency, and failure causes, with findings linked to configurations, raw traces, and delivered artifacts. |

## How It Works

The design combines an **established evaluation backend, a thin control layer, and pluggable adapters**. The backend provides execution, grading, and logging foundations; the control layer manages experimental conditions and run lifecycles; adapters connect individual agent systems.

```text
Task packages + System configurations + Budgets
                       |
                       v
              Evaluation controller
         Run matrix / Isolation / Timeouts / Recovery
                       |
            +----------+----------+
            v          v          v
          Python      CLI        HTTP
            +----------+----------+
                       v
              Agent system under test
               Single- or multi-agent
                       |
                       v
        Answers / Files / State snapshots / Traces
                       |
                       v
                Independent graders
                       |
                       v
          Comparison reports and evidence index

Usage metering and budget control span execution
```

### 1. Evaluate the Whole System

One run represents one task, one system configuration, and one repetition. Model calls from planners, workers, verifiers, and context summarization all belong to the same run identity and budget.

This gives single-agent and multi-agent systems a consistent unit of comparison while retaining opportunities to analyze their internal collaboration.

### 2. Separate Tasks from Grading Criteria

A task package describes the problem, input materials, deliverables, and environment requirements. Reference answers, hidden tests, and grading rules are managed separately on the evaluation side.

Deterministic tests and rules form the basis of grading, supplemented by rubrics, model-based review, or human review for open-ended quality dimensions. Once outputs are saved as snapshots, versioned graders can rescore them using the existing execution results.

### 3. Separate Execution Status from Task Success

Run records distinguish four outcomes:

- **Execution status**: normal completion, timeout, budget exhaustion, or system error.
- **Self-reported status**: whether the system claims to have completed the task or passed its own checks.
- **External grade**: whether the result meets independent acceptance criteria, together with its quality scores.
- **Cleanup status**: whether child tasks and remote resources are confirmed stopped or released.

This makes cases such as “completed normally with an incorrect answer” and “self-reported success with a failing external grade” explicit.

### 4. Match Comparisons to Observability

| Integration level | Evaluation and accounting approach |
| --- | --- |
| **Black-box integration** | Submit tasks, detect completion, and collect outputs to evaluate delivery quality and end-to-end latency. Label self-reported usage with its source and completeness. |
| **Controlled integration** | Attribute every model call to a run, verify models, permissions, and budgets, and validate isolation and cancellation paths for resource-constrained architecture comparisons. |

Strict-budget experiments rely on admission control before model calls: reserve capacity for concurrent requests, reconcile actual usage, and continue accounting for calls still in flight during cancellation. Reports distinguish measured, estimated, and missing data, as well as results from different integration levels.

## Evaluation Tasks

| Task type | Typical deliverables | Grading basis |
| --- | --- | --- |
| **Question answering and research** | Structured answers, research findings, source citations | Reference answers, factual coverage, citation and source checks |
| **File-based delivery** | Documents, spreadsheets, analytical reports | File structure, content rules, completeness, and quality rubrics |
| **Environment interaction** | Code changes, terminal tasks, observable state changes | Hidden tests, state assertions, and regression checks |

Task packages and architecture adapters evolve independently. The same suite can evaluate multiple systems, and the same system can be evaluated on different business tasks and public benchmarks. Task data versions, licensing information, and grader versions are recorded with the experiment.

## Experiments and Metrics

An experiment follows this structure:

```text
Task suite × System configurations × Repetitions
                       ↓
           Run records → Independent grades → Comparison reports
```

Experiments fix or record model versions, tool permissions, budgets, environment resources, and memory and caching policies. Sample-level concurrency is tracked separately from concurrency within a system. Whole-system comparisons support architecture selection; ablation studies investigate individual mechanisms.

| Dimension | Key metrics |
| --- | --- |
| **Quality** | External success rate, task-specific quality scores, agreement between self-reported and independently verified success |
| **Cost** | Input and output tokens, cached usage, monetary cost, cost per successful run |
| **Latency** | End-to-end duration, with separate timings for preparation, queuing, execution, collection, and grading |
| **Reliability** | Timeout rate, budget-exhaustion rate, infrastructure error rate, recovery behavior, and cleanup completeness |
| **Interpretability** | Per-task results, variation across repetitions, failure causes, data gaps, and supporting evidence |

Statistics cover successful and failed runs, including the cost of internal retries. Cost per successful run is total execution cost divided by externally verified successes; it is marked unavailable when there are no successes. External grading and evaluation-control overhead are accounted for separately and included in total experiment expenditure.

Analysis uses task-level paired comparisons, variation across repetitions, and confidence intervals to communicate differences. The reporting design includes Markdown, static HTML, and JSON/CSV exports, linking aggregate findings back to individual run configurations, traces, and artifacts.

## Extensible by Design

The core contracts separate tasks, systems, execution, and grading:

| Object | Responsibility |
| --- | --- |
| `TaskSpec` | Agent-visible problem, materials, deliverables, and environment requirements |
| `GradingSpec` | Evaluation-side reference answers, hidden tests, rules, and grader versions |
| `SystemSpec` | Architecture identity, adapter, model and prompt configuration, code version, and declared capabilities |
| `RunConfig` | Per-run budget, timeout, resources, and repetition identifier |
| `RunResult` | Execution status, answer, artifacts, trace references, usage, and self-reported status |
| `ScoreResult` | Acceptance results, quality scores, and evidence produced by independent graders |

Adapters declare observability and control through `capabilities`, and manage the run lifecycle through `start`, `poll`, `cancel`, `collect`, and `cleanup`. Stable run identities and idempotent start protocols support state reconciliation and recovery after interruptions.

Extensions follow three boundaries: **add an adapter for a new architecture, a task package for a new task, and a grader for a new acceptance method**. The evaluation core maintains a consistent experimental workflow and accounting model.

## Contributing

Contributions are welcome across:

- Agent system adapters and minimal integration examples.
- Reproducible task packages, deterministic tests, and quality rubrics.
- Usage metering, budget control, and isolated environments.
- Tests for timeouts, cancellation, recovery, and resource cleanup.
- Comparison reports, trace analysis, and Chinese and English documentation.

Please include source, version, and licensing information with task packages or datasets. Tasks involving sensitive materials can demonstrate integration through sanitized examples.

## License

This project is licensed under the [Apache License 2.0](<LICENSE>). Third-party task suites, datasets, and dependencies retain their respective licenses.
