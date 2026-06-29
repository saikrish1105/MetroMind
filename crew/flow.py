"""
Flow for the Chennai Metro Assistant.

WHY A FLOW INSTEAD OF Process.hierarchical
-------------------------------------------
CrewAI's Process.hierarchical manager_agent does NOT skip tasks. Internally
(`Crew._execute_tasks` -> `prepare_task_execution`), every task in a crew's
`tasks=[...]` list runs, every time, in order — in hierarchical mode the
manager is just substituted in as the executor for each one
(`Crew._get_agent_to_use`). The manager's delegation tool is an extra tool
it MAY call mid-task; it is not a gate that decides whether a task runs at
all. That's why the original run executed route_query_task, then
answer_faq_task, then train_info_task back-to-back on a single query, each
producing its own "I don't have that information" answer.

A Flow's @router gives us a real, structural guarantee instead: it reads
one upstream result and emits exactly one event, and only the @listen
method(s) matching that one event execute. Nothing else in the flow runs.

WHERE THE "AGENTIC" PART STILL LIVES
-------------------------------------
The classification itself is still a full LLM call (the Supervisor agent,
via router_crew() in crew/crew.py) — this Flow does no keyword matching or
manual if/else on the user's text. The @router below only inspects the
*Supervisor's own structured output* (a RouteDecision.category string) and
maps it to a branch. The Supervisor decides; the Flow just guarantees that
decision is actually respected.
"""

from __future__ import annotations

from typing import Literal

from crewai.flow import Flow, listen, or_, router, start
from pydantic import BaseModel

from crew.crew import ChennaiMetroCrew


class MetroFlowState(BaseModel):
    """Shared state threaded through the flow.

    `query` is populated from kickoff(inputs={"query": ...}) — CrewAI Flow
    matches dict keys in `inputs` to fields on this model by name.
    """

    query: str = ""
    category: str = ""
    answer: str = ""


class ChennaiMetroFlow(Flow[MetroFlowState]):
    """One query in, one answer out. Exactly one specialist crew executes
    per run — guaranteed by @router, not by agent cooperation."""

    @start()
    def classify_query(self) -> None:
        """Run the single-task router_crew() (Supervisor agent) and store
        its structured decision in state. This is the one place an LLM
        decides intent; everything downstream just acts on that decision."""
        result = ChennaiMetroCrew().router_crew().kickoff(
            inputs={"query": self.state.query}
        )
        decision = result.pydantic  # RouteDecision, set via Task.output_pydantic
        category = (decision.category if decision else "unsupported").strip().lower()

        valid_categories = {"info", "train", "support", "travel_history", "unsupported"}
        self.state.category = category if category in valid_categories else "unsupported"

    @router(classify_query)
    def route_by_category(
        self,
    ) -> Literal["info", "train", "support", "travel_history", "unsupported"]:
        """Pure dispatch on the Supervisor's own decision — no second LLM
        call, no keyword matching. Whatever string this returns is the
        ONLY branch that will execute next."""
        return self.state.category  # type: ignore[return-value]

    @listen("info")
    def handle_info(self) -> None:
        result = ChennaiMetroCrew().info_crew().kickoff(
            inputs={"query": self.state.query}
        )
        self.state.answer = str(result)

    @listen("train")
    def handle_train(self) -> None:
        result = ChennaiMetroCrew().train_crew().kickoff(
            inputs={"query": self.state.query}
        )
        self.state.answer = str(result)

    @listen(or_("support", "travel_history", "unsupported"))
    def handle_unsupported(self) -> None:
        result = ChennaiMetroCrew().unsupported_crew().kickoff(
            inputs={"query": self.state.query, "category": self.state.category}
        )
        self.state.answer = str(result)
