import unittest
from unittest.mock import MagicMock

from app.services.bedrock.embeddings import TitanEmbeddings


class TitanEmbeddingsTests(unittest.TestCase):
    def test_embed_texts_calls_invoke_model(self) -> None:
        mock_client = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b'{"embedding": [0.1, 0.2, 0.3]}'
        mock_client.invoke_model.return_value = {"body": mock_body}

        service = TitanEmbeddings(
            model_id="amazon.titan-embed-text-v2:0",
            region="us-east-1",
            dimension=3,
            batch_size=2,
            client=mock_client,
        )
        vectors = service.embed_texts(["hello", "world"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[0], [0.1, 0.2, 0.3])
        self.assertEqual(mock_client.invoke_model.call_count, 2)

    def test_embed_query(self) -> None:
        mock_client = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b'{"embedding": [0.5, 0.6]}'
        mock_client.invoke_model.return_value = {"body": mock_body}

        service = TitanEmbeddings(
            model_id="amazon.titan-embed-text-v2:0",
            region="us-east-1",
            dimension=2,
            client=mock_client,
        )
        vector = service.embed_query("question")
        self.assertEqual(vector, [0.5, 0.6])


if __name__ == "__main__":
    unittest.main()
