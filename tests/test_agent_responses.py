import sys
from pathlib import Path
from unittest.mock import Mock
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agent import _extract_json, run_agent, Tool


def test_extracts_nested_action_despite_fences_and_braces_in_strings():
    value=_extract_json('Explanation {not JSON} ```json\n{"action":"logs","args":{"nested":{"message":"literal } and {"}}}\n```')
    assert value['args']['nested']['message']=='literal } and {'


@pytest.mark.parametrize('text',['[]','42','{"action":[]}', '{"action":"logs","args":[]}', '{"action":"logs"} {"final_answer":"done"}', '{"action":"logs","final_answer":"done"}'])
def test_rejects_ambiguous_or_malformed_envelopes(text):
    with pytest.raises(ValueError):_extract_json(text)


@pytest.mark.parametrize('answer',['', '...', '…', None, [], {}])
def test_degenerate_answers_are_retried(answer):
    import json
    llm=Mock();llm.chat.side_effect=[json.dumps({'final_answer':answer}),'{"final_answer":"The workload is healthy based on its status."}']
    result=run_agent(llm,{},'Inspect','task',verbose=False)
    assert result.startswith('The workload')
    assert llm.chat.call_count==2


def test_valid_tool_execution_still_requires_evidence_before_final():
    llm=Mock();llm.chat.side_effect=['{"final_answer":"Too early"}','{"action":"logs","args":{}}','{"final_answer":"Crash is caused by a missing configuration file."}']
    tool=Mock(return_value='missing config')
    result=run_agent(llm,{'logs':Tool('logs','Read logs',tool)},'Inspect','task',verbose=False,require_any_before_final={'logs'})
    tool.assert_called_once_with()
    assert 'missing configuration' in result
