import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "transport" / "receiver.py"
spec = importlib.util.spec_from_file_location("receiver", path)

class ReceiverTests(unittest.TestCase):
    def test_receiver_exists_and_validates_destination(self):
        self.assertTrue(path.exists(), "Receiver entry point has not been created")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertFalse(module.is_addressed_offer({"sender_dev_id": 1, "to_dev_id": 2}, 3))
        self.assertFalse(module.is_addressed_offer({"sender_dev_id": None, "to_dev_id": 3}, 3))
        self.assertTrue(module.is_addressed_offer({"sender_dev_id": 1, "to_dev_id": 3}, 3))

    def test_file_integrity(self):
        self.assertTrue(path.exists())
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.check_integrity(3, 3, "abc", "ABC")
        with self.assertRaises(ValueError):
            module.check_integrity(2, 3, "abc", None)
        with self.assertRaises(ValueError):
            module.check_integrity(3, 3, "abc", "xyz")

if __name__ == "__main__":
    unittest.main()
