import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from resize_workers import plan, apply


FAKE = '''import argparse,fcntl,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--root',type=Path)
p.add_argument('--config',type=Path);p.add_argument('--export-output',type=Path);p.add_argument('--limit')
a=p.parse_args();workers=json.loads(a.config.read_text())['workers'];root=a.root
with (root/'coordinator.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if workers==3:
        (root/'old-started').touch()
        until=time.monotonic()+10
        while not (root/'drain.request.json').exists():
            if time.monotonic()>until: raise RuntimeError('No drain received')
            time.sleep(.01)
        (root/'queue/00001.json').write_text(json.dumps({'state':'completed','sentinel':'keep'}))
    else:
        assert (root/'export-finished').exists(), 'Restart raced the old export'
        assert not (root/'drain.request.json').exists()
        (root/'progress.json').write_text(json.dumps({'workers':workers,'updated_at':time.time(),'states':{'completed':1,'pending':1}}))
        time.sleep(30)
if workers==3:
    time.sleep(.1)
    (root/'export-finished').touch()
'''


class ResizeTests(unittest.TestCase):
    def fixture(self,root):
        (root/'queue').mkdir()
        (root/'queue/00001.json').write_text(json.dumps({'state':'running'}))
        (root/'queue/00002.json').write_text(json.dumps({'state':'pending','sentinel':'keep-pending'}))
        config={'workers':3,'teacher':'same-teacher','reviewer':'same-reviewer',
                'reasoning_effort':'medium','instructions':'frozen prompt','wire_protocol':'same'}
        path=root/'config.json';path.write_text(json.dumps(config))
        script=root/'corpus_workers.py';script.write_text(FAKE)
        command=[sys.executable,str(script),'run','--root',str(root),'--limit','3015',
                 '--config',str(path),'--export-output',str(root/'old-export')]
        (root/'old-launch.json').write_text(json.dumps({'command':command}))
        (root/'active-launch.json').write_text(json.dumps({'record':'old-launch.json','pid':999999999}))
        return command

    def test_plan_preserves_prompt_model_and_changes_only_concurrency_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);command=self.fixture(root)
            change=plan(root,6)
            self.assertEqual(change['workers_before'],3)
            self.assertEqual(change['config']['workers'],6)
            for key in ['teacher','reviewer','instructions','reasoning_effort','wire_protocol']:
                self.assertEqual(change['config'][key],json.loads((root/'config.json').read_text())[key])
            self.assertEqual(change['previous_command'],command)
            self.assertFalse((root/'drain.request.json').exists())
            self.assertFalse(Path(change['config_path']).exists())

    def test_existing_drain_and_failed_jobs_are_not_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            flag=root/'drain.request.json';flag.write_text('{"operator":"other"}')
            with self.assertRaisesRegex(ValueError,'Another drain'):apply(root,6)
            self.assertEqual(flag.read_text(),'{"operator":"other"}')
            (root/'queue/00002.json').write_text('{"state":"needs_review"}')
            with self.assertRaisesRegex(ValueError,'failed/review'):plan(root,6)

    @unittest.skipUnless(Path('/proc/self/cmdline').exists(),'Linux process identity check')
    def test_drain_waits_for_export_then_launches_six_without_resetting_batches(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);command=self.fixture(root)
            old=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            replacement=None
            children=[]
            real_popen=subprocess.Popen
            def launch(*args,**kwargs):
                child=real_popen(*args,**kwargs)
                children.append(child)
                return child
            try:
                deadline=time.monotonic()+5
                while not (root/'old-started').exists():
                    if time.monotonic()>deadline:self.fail('Fixture failed to start')
                    time.sleep(.01)
                (root/'active-launch.json').write_text(json.dumps({'record':'old-launch.json','pid':old.pid}))
                with patch('resize_workers.subprocess.Popen',side_effect=launch):
                    result=apply(root,6,timeout=5)
                replacement=result['pid']
                self.assertEqual(result['stage'],'running')
                self.assertEqual(result['workers'],6)
                self.assertEqual(json.loads((root/'queue/00001.json').read_text()),{'state':'completed','sentinel':'keep'})
                self.assertEqual(json.loads((root/'queue/00002.json').read_text()),{'state':'pending','sentinel':'keep-pending'})
                self.assertTrue((root/'export-finished').exists())
                self.assertFalse((root/'drain.request.json').exists())
                self.assertEqual(json.loads((root/'active-launch.json').read_text())['pid'],replacement)
            finally:
                for child in children:
                    child.terminate();child.wait(timeout=5)
                old.terminate();old.wait(timeout=5)


if __name__=='__main__':unittest.main()
