import os
import requests
from abc import ABC, abstractmethod

class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Generate a 768-dimension vector embedding for the input text."""
        pass


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def embed_text(self, text: str) -> list[float]:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured")
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={self.api_key}"
        payload = {
            "model": "models/text-embedding-004",
            "content": {
                "parts": [{"text": text}]
            }
        }
        r = requests.post(url, json=payload, timeout=10.0)
        if r.status_code == 200:
            return r.json()["embedding"]["values"]
        else:
            raise Exception(f"Gemini API returned status {r.status_code}: {r.text}")


class MockEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimension: int = 768):
        self.dimension = dimension

    def embed_text(self, text: str) -> list[float]:
        # Return a deterministic mock vector based on string length & characters
        val = len(text) / 1000.0
        return [val] + [0.0] * (self.dimension - 1)


class EmbeddingProviderRegistry:
    _providers = {}

    @classmethod
    def register(cls, name: str, provider: EmbeddingProvider):
        cls._providers[name.lower()] = provider

    @classmethod
    def get_provider(cls, name: str) -> EmbeddingProvider:
        name_lower = name.lower()
        if name_lower not in cls._providers:
            # Auto-instantiate based on name
            if name_lower == "gemini":
                cls._providers["gemini"] = GeminiEmbeddingProvider()
            elif name_lower == "mock":
                cls._providers["mock"] = MockEmbeddingProvider()
            else:
                raise ValueError(f"Unknown embedding provider: {name}")
        return cls._providers[name_lower]

# Pre-register default providers
EmbeddingProviderRegistry.register("gemini", GeminiEmbeddingProvider())
EmbeddingProviderRegistry.register("mock", MockEmbeddingProvider())
