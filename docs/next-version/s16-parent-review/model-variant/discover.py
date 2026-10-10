"""Read the configured provider's model IDs; never expose credentials."""
import asyncio
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from code_agent.config.loader import load_runtime_config
from code_agent.providers.model_discovery import discover_models


async def main():
    runtime = load_runtime_config(cli_profile='glm-5-3-flash')
    models = await discover_models(runtime.provider)
    result = {'profile': runtime.profile, 'base_url': runtime.provider.base_url,
              'model_ids': models, 'inference_calls': 0}
    (HERE / 'model-catalog.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
