from __future__ import annotations

import threading
import unittest

from dairack.model_ops import TransferCancelled, pull_model, remove_model, validate_model_name


class FakeProvider:
    def __init__(self) -> None:
        self.deleted = ""

    def pull(self, model: str):
        yield {"status": "pulling manifest"}
        yield {"status": "downloading", "digest": "a", "completed": 25, "total": 100}
        yield {"status": "downloading", "digest": "a", "completed": 100, "total": 100}
        yield {"status": "success"}

    def delete(self, model: str) -> None:
        self.deleted = model


class ModelOperationTests(unittest.TestCase):
    def test_pull_reports_structured_aggregate_progress(self) -> None:
        progress = []
        result = pull_model(FakeProvider(), "registry:5000/team/model:tag", on_progress=progress.append)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.completed, 100)
        self.assertEqual(progress[-2].percent, 1.0)

    def test_pull_can_be_cancelled_between_stream_events(self) -> None:
        cancelled = threading.Event()

        def stop(_progress):
            cancelled.set()

        with self.assertRaises(TransferCancelled):
            pull_model(FakeProvider(), "example:latest", cancel_event=cancelled, on_progress=stop)

    def test_remove_and_name_validation_are_api_safe(self) -> None:
        provider = FakeProvider()
        self.assertEqual(validate_model_name("host:5000/team/model@sha256:abc"), "host:5000/team/model@sha256:abc")
        with self.assertRaises(ValueError):
            validate_model_name("model name")
        with self.assertRaises(ValueError):
            validate_model_name("model|ui-delimiter")
        self.assertEqual(remove_model(provider, "example:latest"), "example:latest")
        self.assertEqual(provider.deleted, "example:latest")


if __name__ == "__main__":
    unittest.main()
