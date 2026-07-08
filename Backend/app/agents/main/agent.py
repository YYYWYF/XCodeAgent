from deepagents import CompiledSubAgent, create_deep_agent

from app.tools.ask_user import ask_user


def create_main_agent(model, frontend, data_source, test):
    return create_deep_agent(
        name="main-agent",
        model=model,
        tools=[ask_user],
        system_prompt=(
            "You are the application-generation coordinator. Analyze requirements, "
            "create and update RequirementSpec documents, clarify uncertain requirements, "
            "create project-level plans, define API/page/data-source contracts, "
            "coordinate detail confirmation, and delegate implementation and testing when appropriate. "
            "Use ask_user when user input is required before safe planning can continue. "
            "Keep responses concise in this minimal demo."
        ),
        subagents=[
            CompiledSubAgent(
                name="frontend-generation-agent",
                description="Generates frontend pages from approved plans.",
                runnable=frontend,
            ),
            CompiledSubAgent(
                name="data-source-generation-agent",
                description="Generates data sources, backend APIs, and seed data.",
                runnable=data_source,
            ),
            CompiledSubAgent(
                name="test-agent",
                description="Runs integration and end-to-end checks.",
                runnable=test,
            ),
        ],
    )
