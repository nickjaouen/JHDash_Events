## A tool to call the openAI API to get embeddings for a text string
from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
@tool
def get_embeddings(text: str) -> list[float]:
    """Get embeddings for a text string"""
    return embeddings.embed_query(text)

__all__ = ["get_embeddings"]