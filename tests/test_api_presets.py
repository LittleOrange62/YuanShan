import pytest
from review_agent.runtime import connection, config_for
from review_agent.evidence import EvidenceError

@pytest.mark.parametrize('url', [
    'https://api.deepseek.com', 'https://openrouter.ai/api/v1',
    'https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
    'https://api.siliconflow.cn/v1', 'https://api.minimaxi.com/v1',
    'https://api.groq.com/openai/v1', 'https://api.together.xyz/v1',
])
def test_api_preset_reaches_responses_engine_unchanged(tmp_path, url):
    source, key = connection({'provider':'api-compatible','baseURL':url,'model':'custom/model','apiKey':'TEST_ONLY_KEY'})
    config = config_for(tmp_path, source)
    assert config['model_providers.astock_api']['base_url'] == url
    assert config['model_providers.astock_api']['wire_api'] == 'responses'
    assert source['model'] == 'custom/model'
    assert key == 'TEST_ONLY_KEY' and key not in repr(config)

@pytest.mark.parametrize('url', ['https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', 'https://host/<workspace>/v1'])
def test_unfilled_endpoint_placeholder_is_rejected(url):
    with pytest.raises(EvidenceError):
        connection({'provider':'api-compatible','baseURL':url,'model':'qwen','apiKey':'TEST_ONLY_KEY'})
