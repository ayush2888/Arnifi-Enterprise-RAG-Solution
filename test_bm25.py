import os
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
#from langchain_bedrock.embeddings import BedrockEmbeddings
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from langchain.retrievers import EnsembleRetriever
os.environ.setdefault("HUGGINGFACEHUB_API_TOKEN", os.getenv("HUGGINGFACEHUB_API_TOKEN", ""))
if not os.environ["HUGGINGFACEHUB_API_TOKEN"]:
    raise RuntimeError("Set HUGGINGFACEHUB_API_TOKEN in your environment before running this script.")

embeddings = HuggingFaceEndpointEmbeddings(
    model = "sentence-transformers/all-MiniLM-L6-v2",
    task = "feature-extraction"
)


docs = [
    Document(page_content="The iPhone 15 Pro uses an A17 Pro chip and has a titanium frame.", metadata={"source": "tech_specs"}),
    Document(page_content="Apple was founded in 1976 by Steve Jobs, Steve Wozniak, and Ronald Wayne.", metadata={"source": "history"}),
    Document(page_content="The standard warranty for new Apple products is one year from purchase.", metadata={"source": "policy"}),
    Document(page_content="To reset your iPhone, go to Settings > General > Transfer or Reset iPhone.", metadata={"source": "support"})
]

bm25_retriever = BM25Retriever.from_documents(docs)
bm25_retriever.k = 2


vectorstore = Chroma.from_documents(docs, embeddings())
#as_retriever is a method that returns a retriever object that can be used to retrieve documents from the vectorstore
vector_retriever = vectorstore.as_retriever(search_kwargs={"k": 2})

ensemble_retriever = EnsembleRetriever(
    retrivers = [bm25_retriever, vector_retriever],
    weights = [0.5, 0.5]
)

hybrid_docs = ensemble_retriever.invoke("What is the warranty for new Apple products?")
print(hybrid_docs)

# class Dog:
#     def __init__(self, name):
#         self.name = name
    
#     def bark(self):
#         print("Woof")

