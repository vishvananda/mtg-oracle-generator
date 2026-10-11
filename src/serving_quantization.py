"""Controlled GGUF quantization comparison on a frozen development subset.

Each quant starts from the same F16 source, never from another quant. Runtime,
prompt, sampling and context remain fixed. This is not a browser speed test.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

from rl_gpu import save, sha
from rl_serving_eval import generate, evaluate


def post(endpoint, route, payload):
    request = urllib.request.Request(endpoint+route, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def quantize(source, destination, kind, quantizer):
    identity = {'source_sha256': sha(source), 'quantizer_sha256': sha(quantizer),
                'quantization': kind, 'recipe': 'one quantization of the same F16 GGUF'}
    receipt = destination.with_suffix('.json')
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if any(saved[k] != v for k, v in identity.items()) or sha(destination) != saved['sha256']:
            raise ValueError('Frozen quantization changed')
        return saved
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix('.pending.gguf')
    with destination.with_suffix('.log').open('w') as log:
        subprocess.run([str(quantizer), str(source), str(pending), kind, '8'],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    pending.rename(destination)
    result = {**identity, 'sha256': sha(destination), 'bytes': destination.stat().st_size}
    save(receipt, result)
    return result


def run_arm(root, arm, port=4241):
    spec = json.loads((root/'experiment.json').read_text())
    setup = spec['arms'][arm]
    model = Path(setup['model']); server = Path(spec['server'])
    if setup.get('source'):
        quantize(Path(setup['source']), model, setup['quantization'], Path(spec['quantizer']))
    identity = {'model_sha256': sha(model), 'server_sha256': sha(server),
                'cases_sha256': sha(root/'cases.jsonl'), 'system_sha256': sha(root/'system-prompt.txt'),
                'threads': 8, 'slots': 4, 'context_per_slot': 4096,
                'quantization': setup['quantization'], 'scope': 'native CPU quality, not browser GPU speed'}
    if setup.get('adapter'):
        identity['adapter_sha256'] = sha(Path(setup['adapter']))
    output = root/'predictions'; output.mkdir(exist_ok=True)
    pin = output/(arm+'-identity.json')
    if pin.exists() and json.loads(pin.read_text()) != identity:
        raise ValueError('Serving experiment inputs changed')
    save(pin, identity)
    endpoint = f'http://127.0.0.1:{port}'
    def sample():
        return generate(root/'cases.jsonl', (root/'system-prompt.txt').read_text(),
            identity['model_sha256'], endpoint, output/(arm+'.jsonl'), 4,
            backend='llama.cpp '+setup['quantization']+' CPU'+(' + F16 LoRA' if setup.get('adapter') else ''),
            adapter_sha=identity.get('adapter_sha256'))
    receipt = output/(arm+'.manifest.json')
    if receipt.exists() and json.loads(receipt.read_text()).get('complete'):
        return sample()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
    command = [str(server), '--model', str(model), '--host', '127.0.0.1', '--port', str(port),
        '--threads', '8', '--threads-batch', '8', '--ctx-size', '16384', '--parallel', '4',
        '--jinja', '--no-webui', '--chat-template-kwargs', '{"enable_thinking":false}']
    if setup.get('adapter'):
        command.extend(['--lora', setup['adapter']])
    save(output/(arm+'-command.json'), command)
    with (output/(arm+'-server.log')).open('a') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+120
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Evaluation server exited; inspect log')
                try:
                    with urllib.request.urlopen(endpoint+'/health', timeout=2) as response:
                        if json.load(response).get('status') == 'ok':
                            break
                except (OSError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError('Evaluation server startup')
                time.sleep(.5)
            # Compare tokenizer/template bytes against frozen Transformers output.
            audit = []
            for row in map(json.loads, (root/'prompt-audit.jsonl').read_text().splitlines()):
                rendered = post(endpoint, '/apply-template', {'messages': row['messages'],
                    'add_generation_prompt': True, 'chat_template_kwargs': {'enable_thinking': False}})['prompt']
                tokens = post(endpoint, '/tokenize', {'content': rendered, 'add_special': False})['tokens']
                audit.append({'id': row['id'], 'text_equal': rendered == row['rendered'],
                              'tokens_equal': tokens == row['tokens']})
            save(output/(arm+'-prompt-audit.json'), audit)
            if any(not x['text_equal'] or not x['tokens_equal'] for x in audit):
                raise ValueError('Prompt or tokenizer drift; inspect before comparing quantizations')
            return sample()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def run(root):
    spec = json.loads((root/'experiment.json').read_text())
    arms = list(spec['arms'])
    for offset in range(0, len(arms), 2):
        pair = arms[offset:offset+2]
        save(root/'status.json', {'phase': 'generating', 'arms': pair})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_arm, root, arm, spec.get('port_base', 4241)+i) for i, arm in enumerate(pair)]
            for future in futures:
                future.result()
    if spec.get('generation_only'):
        save(root/'status.json', {'phase': 'ready_for_joint_evaluation'})
        return
    save(root/'status.json', {'phase': 'evaluating'})
    result = evaluate(root/'cases.jsonl', {arm: root/'predictions'/(arm+'.jsonl') for arm in spec['arms']},
                      root/'evaluation', workers=3, display_names=True)
    save(root/'status.json', {'phase': 'complete', 'metrics': result['metrics'], 'automatic_promotion': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    run(parser.parse_args().root)
