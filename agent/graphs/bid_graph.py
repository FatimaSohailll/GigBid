# TODO: Build a hand-written LangGraph StateGraph.
# Required design: parallel Job Analyst, Risk Assessor and Fit Matcher branches;
# decision routing; pricing; writer; claim verification; Critic (maximum two revisions);
# Compliance Guard; interrupt-based human approval before any external write.
#
# Do not replace the required graph with a linear chain labelled "multi-agent".

from typing import NoReturn


def build_bid_graph() -> NoReturn:
    raise NotImplementedError("Implement the bid graph as specified in the assignment.")
