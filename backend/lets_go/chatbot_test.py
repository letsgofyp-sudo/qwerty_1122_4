# pip install langchain langchain-community langchain-ollama langgraph
from typing import Annotated, Literal, TypedDict
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import ToolNode
from langgraph.graph import StateGraph, START, END

# --- STEP 1: Define your Tools (APIs/DB Lookups) ---
@tool
def check_order_status(order_id: str):
    """Refers to the internal DB to find order shipping status."""
    # In a real Django app, you'd do: Order.objects.get(id=order_id)
    mock_db = {"123": "Shipped", "456": "Processing"}
    return f"The status of order {order_id} is: {mock_db.get(order_id, 'Not Found')}"

@tool
def search_manual(query: str):
    """Searches the User Manual and FAQs for technical instructions."""
    # Simulated RAG/Vector search response
    return "Manual says: To reset the device, hold the power button for 10 seconds."

tools = [check_order_status, search_manual]
model = ChatOllama(model="llama3.2", temperature=0).bind_tools(tools)

# --- STEP 2: Define Agent Logic ---
class State(TypedDict):
    messages: Annotated[list, "The messages in the conversation"]

def call_model(state: State):
    response = model.invoke(state["messages"])
    return {"messages": [response]}

# --- STEP 3: Build the Graph ---
workflow = StateGraph(State)
workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(tools))

# Logic: If model called a tool, go to tools; otherwise, end.
def should_continue(state: State):
    last_message = state["messages"][-1]
    return "tools" if last_message.tool_calls else END

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue)
workflow.add_edge("tools", "agent")

app = workflow.compile()

# --- STEP 4: Test it ---
def ask_bot(question: str):
    inputs = {"messages": [("user", question)]}
    for output in app.stream(inputs):
        for key, value in output.items():
            if key == "agent" and value["messages"][-1].content:
                print(f"Bot: {value['messages'][-1].content}")

# Try asking about the DB or the Manual:
print("--- Querying DB Tool ---")
ask_bot("What is the status of order 123?")

print("\n--- Querying Manual Tool ---")
ask_bot("How do I reset my device?")
