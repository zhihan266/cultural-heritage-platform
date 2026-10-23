"""Register explicit model configurations; dry-run by default, never embeds keys."""

import argparse
import json
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default='http://127.0.0.1:8000')
    parser.add_argument('--llm-url', required=True, help='Complete chat/completions endpoint')
    parser.add_argument('--llm-model', required=True, help='Model ID accepted by the selected provider')
    parser.add_argument('--auth-type', choices=['none', 'bearer'], default='bearer')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--check-health', action='store_true', help='Explicitly sends model health requests, potentially billable')
    args = parser.parse_args()
    if urlsplit(args.api_url).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        parser.error('Registration API must be loopback')
    endpoint = urlsplit(args.llm_url)
    if endpoint.scheme not in {'http', 'https'} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query:
        parser.error('LLM endpoint must be an HTTP(S) URL without embedded credentials')
    payloads = [{'name': 'competition-' + name, 'type': role, 'api_url': args.llm_url,
                     'provider': 'openai_compatible', 'model_name': args.llm_model, 'initial_score': 1.0,
                     'auth_type': args.auth_type, 'credential_ref': 'DEEPSEEK_API_KEY' if args.auth_type == 'bearer' else None}
                for name, role in [('worker-1', 'worker'), ('worker-2', 'worker'), ('judge', 'judge')]]
    if not args.apply:
        print(json.dumps({'dry_run': True, 'agents': payloads}, indent=2))
        return
    import httpx
    result = {}
    with httpx.Client(base_url=args.api_url, timeout=180, trust_env=False) as client:
        listed = client.get('/ais')
        listed.raise_for_status()
        existing = {item['name']: item for item in listed.json()}
        for payload in payloads:
            item = existing.get(payload['name'])
            if item is None:
                response = client.post('/ais', json=payload)
                response.raise_for_status()
                item = response.json()['ai']
            elif any(item.get(key) != payload[key] for key in ['api_url', 'type', 'model_name', 'auth_type', 'credential_ref']):
                raise ValueError('Existing named Agent differs; review configuration before proceeding')
            result[payload['name']] = item['id']
            if args.check_health:
                response = client.post('/ais/' + item['id'] + '/health-check')
                response.raise_for_status()
                if not response.json()['available']:
                    raise RuntimeError('Agent health check failed; inspect the local management page')
    print(json.dumps({'agent_ids': result, 'aggregator_setting': result['competition-judge']}, indent=2))


if __name__ == '__main__':
    main()
