import errno
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from serving_quantization import available_port
from sft_checkpoint_review import run, save_status


class ReviewRecoveryTests(unittest.TestCase):
    def test_ephemeral_port_does_not_choose_an_active_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            occupied = listener.getsockname()[1]
            self.assertNotEqual(available_port(), occupied)
            with self.assertRaises(OSError) as failure:
                available_port(occupied)
            self.assertEqual(failure.exception.errno, errno.EADDRINUSE)

    def test_closed_connections_do_not_block_the_next_model(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0)); listener.listen()
            port = listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1', port)) as client:
                accepted, _ = listener.accept()
                accepted.shutdown(socket.SHUT_WR)
                self.assertEqual(client.recv(1), b'')
                accepted.close()
        self.assertEqual(available_port(port), port)

    def test_failure_is_reported_without_deleting_completed_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            review = Path(folder)
            saved = review/'original-sft.jsonl'; saved.write_text('preserved output\n')
            save_status(review, {'phase': 'generating', 'arm': 'deployed-dpo2'})
            error = OSError(errno.EADDRINUSE, 'Address already in use')
            with patch('sft_checkpoint_review._run', side_effect=error):
                with self.assertRaises(OSError):
                    run(review/'training', review)
            status = json.loads((review/'status.json').read_text())
            self.assertEqual(status['phase'], 'failed')
            self.assertEqual(status['failed_phase'], 'generating')
            self.assertEqual(status['arm'], 'deployed-dpo2')
            self.assertEqual(status['errno'], errno.EADDRINUSE)
            self.assertTrue(status['completed_outputs_preserved'])
            self.assertIn('updated_at', status)
            self.assertEqual(saved.read_text(), 'preserved output\n')
