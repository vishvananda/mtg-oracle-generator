"""Compare two frozen GGUFs locally without changing the deployed model."""
import argparse
import json
import socket
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from custom_sft_experiment import panel_report
from rl_data import sha_file
from rl_serving_eval import evaluate, generate


def run(cases, system, custom, dpo, server, output, port, threads, slots, judges, generate_arm=None):
    output.mkdir(parents=True, exist_ok=True)
    identity = {'cases_sha256': sha_file(cases), 'system_sha256': sha_file(system),
                'models': {k: sha_file(p) for k, p in [('custom', custom), ('dpo', dpo)]},
                'server_sha256': sha_file(server), 'threads': threads, 'slots': slots,
                'context_per_slot': 4096, 'runtime': 'native llama.cpp CPU, Q4_0',
                'scope': 'quantization quality check; not browser GPU performance',
                'automatic_promotion': False}
    pin = output / 'identity.json'
    if pin.exists() and json.loads(pin.read_text()) != identity:
        raise ValueError('Comparison inputs changed')
    pin.write_text(json.dumps(identity, indent=2) + '\n')

    def arm(name, model, listen):
        endpoint = f'http://127.0.0.1:{listen}'
        receipt = output / f'{name}.manifest.json'
        if receipt.exists() and json.loads(receipt.read_text()).get('complete'):
            # Recheck frozen inputs and output bytes without loading a model or
            # binding a recently closed server socket just to reuse predictions.
            return generate(cases, system.read_text(), identity['models'][name], endpoint,
                            output / f'{name}.jsonl', slots)
        # Refuse an occupied port instead of accidentally querying another model.
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', listen))
        command = [str(server), '--model', str(model), '--host', '127.0.0.1',
                   '--port', str(listen), '--threads', str(threads), '--threads-batch', str(threads),
                   '--ctx-size', str(slots * 4096), '--parallel', str(slots), '--jinja',
                   '--no-webui', '--chat-template-kwargs', '{"enable_thinking":false}']
        (output / f'{name}-command.json').write_text(json.dumps(command, indent=2) + '\n')
        with (output / f'{name}-server.log').open('a') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 120
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f'{name} server exited; inspect its log')
                    try:
                        with urllib.request.urlopen(endpoint + '/health', timeout=2) as response:
                            if json.load(response).get('status') == 'ok':
                                break
                    except (OSError, ValueError):
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'{name} server startup timed out')
                    time.sleep(.5)
                return generate(cases, system.read_text(), identity['models'][name], endpoint,
                                output / f'{name}.jsonl', slots)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(arm, name, model, port + i)
                   for i, (name, model) in enumerate([('custom', custom), ('dpo', dpo)])
                   if generate_arm is None or name == generate_arm]
        for future in futures:
            future.result()
    if generate_arm is not None:
        return
    evaluate(cases, {name: output / f'{name}.jsonl' for name in ['custom', 'dpo']},
             output / 'comparison', judges, display_names=True)
    report = panel_report(cases, output / 'comparison/cases.jsonl')
    report['inference'] = identity['runtime']
    report['identity'] = identity
    (output / 'panels.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ['cases', 'system', 'custom', 'dpo', 'server', 'output']:
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--port', type=int, default=4221)
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--slots', type=int, default=4)
    parser.add_argument('--judges', type=int, default=3)
    parser.add_argument('--generate-arm', choices=['custom', 'dpo'], help='Resume only one arm; defer evaluation until both are complete')
    run(**vars(parser.parse_args()))
