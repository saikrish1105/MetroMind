"""
Crew definitions for the Chennai Metro Assistant.

Prompts (role/goal/backstory for agents, description/expected_output for
tasks) live in crew/config/agents.yaml and crew/config/tasks.yaml — this
file only contains wiring: which LLM, which MCP tools, which agent owns
which task.

ARCHITECTURE — read this before changing anything here or in flow.py
---------------------------------------------------------------------
This used to be a single Process.hierarchical Crew with a manager_agent
(the Supervisor) and three worker tasks (route_query_task, answer_faq_task,
train_info_task) all listed in one `tasks=[...]`. That doesn't work in
CrewAI: Process.hierarchical does NOT let the manager skip tasks it
didn't delegate to. Internally, `Crew._execute_tasks` just iterates
`self.tasks` in order and runs every single one — in hierarchical mode it
swaps in `manager_agent` as the executor for each task
(`Crew._get_agent_to_use`), but it still executes ALL of them. The
manager's delegation tool is just an extra tool it CAN call mid-task; it
is not a gate that skips tasks. That's exactly why the original log
showed route_query_task, answer_faq_task, AND train_info_task all running
back-to-back on a single query, each one failing in its own way.

The fix: use a CrewAI Flow (see crew/flow.py) for the skeleton, and keep
the actual routing *decision* as a real agentic step — the Supervisor LLM
classifies the query, and the Flow's @router reads that classification
and emits exactly one event. Only the @listen method matching that one
event runs, so only one specialist crew (and one set of MCP tools) ever
executes per query. This file defines four small, independent crews —
each with exactly one task — that the Flow assembles and kicks off one at
a time:

    router_crew()      -> Supervisor only, no tools, classifies intent
    info_crew()        -> General Info Agent + qdrant_mcp
    train_crew()       -> Train Info Agent + graph_mcp + fare_mcp
    unsupported_crew() -> Supervisor only, explains what isn't built yet

Each agent only gets the MCP tools it actually needs — the original code
attached qdrant_mcp + graph_mcp + fare_mcp all onto general_info_agent
(probably to make sure *something* had the tools, while the architecture
was getting sorted out), which both gave the FAQ agent tools it had no
task instructions for and meant Train Info Agent's own dedicated tools
never got used for train_info_task at all.
"""

from __future__ import annotations

import sys

from crewai import Agent, Crew, Process, Task
from crewai.mcp import MCPServerStdio
from crewai.project import CrewBase, agent, crew, task
from pydantic import BaseModel, Field

from crew.llm_config import chat_llm

# ---------------------------------------------------------------------------
# MCP server connections. Each is its own subprocess/server, one per backing
# data store, kept independent so either can fail or restart without
# affecting the others. cache_tools_list=True is safe for all three: each
# server's tool list is fixed and never changes mid-run.
# ---------------------------------------------------------------------------
qdrant_mcp_server = MCPServerStdio(
    command=sys.executable,
    args=["-m", "mcp_servers.qdrant_mcp.server"],
    cache_tools_list=True,
)

graph_mcp_server = MCPServerStdio(
    command=sys.executable,
    args=["-m", "mcp_servers.graph_mcp.server"],
    cache_tools_list=True,
)

fare_mcp_server = MCPServerStdio(
    command=sys.executable,
    args=["-m", "mcp_servers.fare_mcp.server"],
    cache_tools_list=True,
)


class RouteDecision(BaseModel):
    """Structured output for route_query_task.

    Forcing this via Task.output_pydantic means we read decision.category
    directly in crew/flow.py's @router instead of regex-parsing free text
    out of a small 8B model's reply, which is much less reliable.
    """

    category: str = Field(
        description=(
            "One of: 'info', 'train', 'support', 'travel_history', "
            "'unsupported'."
        )
    )


@CrewBase
class ChennaiMetroCrew:
    """Builds the individual single-task crews used by crew/flow.py.

    Each @crew-decorated method below returns a fully independent Crew
    with exactly one agent and one task under Process.sequential. There is
    no manager_agent and no delegation anywhere in this file — branching
    between these crews happens once, in the Flow, not repeatedly inside
    CrewAI's own task loop.
    """

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # -- Agents ----------------------------------------------------------

    @agent
    def supervisor(self) -> Agent:
        """Intent classification only — no tools, no MCP, no delegation.
        Used by both router_crew() (to classify) and unsupported_crew()
        (to explain what isn't built yet)."""
        return Agent(
            config=self.agents_config["supervisor"],
            llm=chat_llm,
            allow_delegation=False,
            verbose=True,
        )

    @agent
    def general_info_agent(self) -> Agent:
        """FAQ specialist, grounded in Qdrant retrieval via MCP. Only the
        qdrant tool — this agent never touches routing or fare lookups."""
        return Agent(
            config=self.agents_config["general_info_agent"],
            llm=chat_llm,
            mcps=[qdrant_mcp_server],
            allow_delegation=False,
            verbose=True,
        )

    @agent
    def train_info_agent(self) -> Agent:
        """Routing + fare specialist. Has BOTH the graph_mcp and fare_mcp
        tools attached, and decides per-query which one(s) to call — see
        train_info_task's description in tasks.yaml for the exact
        decision rule given to the LLM."""
        return Agent(
            config=self.agents_config["train_info_agent"],
            llm=chat_llm,
            mcps=[graph_mcp_server, fare_mcp_server],
            allow_delegation=False,
            verbose=True,
        )

    # -- Tasks -------------------------------------------------------------

    @task
    def route_query_task(self) -> Task:
        t = Task(
            config=self.tasks_config["route_query_task"],
            agent=self.supervisor(),
        )
        t.output_pydantic = RouteDecision
        return t

    @task
    def answer_faq_task(self) -> Task:
        return Task(
            config=self.tasks_config["answer_faq_task"],
            agent=self.general_info_agent(),
        )

    @task
    def train_info_task(self) -> Task:
        return Task(
            config=self.tasks_config["train_info_task"],
            agent=self.train_info_agent(),
        )

    @task
    def unsupported_task(self) -> Task:
        return Task(
            config=self.tasks_config["unsupported_task"],
            agent=self.supervisor(),
        )

    # -- Crews (one task each — see module docstring) -----------------------

    @crew
    def router_crew(self) -> Crew:
        return Crew(
            agents=[self.supervisor()],
            tasks=[self.route_query_task()],
            process=Process.sequential,
            verbose=True,
        )

    @crew
    def info_crew(self) -> Crew:
        return Crew(
            agents=[self.general_info_agent()],
            tasks=[self.answer_faq_task()],
            process=Process.sequential,
            verbose=True,
        )

    @crew
    def train_crew(self) -> Crew:
        return Crew(
            agents=[self.train_info_agent()],
            tasks=[self.train_info_task()],
            process=Process.sequential,
            verbose=True,
        )

    @crew
    def unsupported_crew(self) -> Crew:
        return Crew(
            agents=[self.supervisor()],
            tasks=[self.unsupported_task()],
            process=Process.sequential,
            verbose=True,
        )