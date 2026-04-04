## A tool to call the tavily API to search the web for information
from langchain_core.tools import tool
from tavily import TavilyClient
import os
from dotenv import load_dotenv
_ = load_dotenv()

tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

@tool
def search_web(query: str) -> str:
    """Search the web for information"""
    return tavily.search(query=query, max_results=3)

__all__ = ["search_web"]