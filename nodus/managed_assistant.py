"""Owned text assistant that saves conversation state with its journaled answer."""

import base64
import json
import os
from pathlib import Path

from . import _agent
from ._agent_models import text_messages
from ._local_files import open_directory
from ._steps import step
from .errors import ValidationError, StepOutcomeUnknown


_STATE_FILE = 'conversation.json'


def _conversation(directory):
    try:
        with directory.open_read(_STATE_FILE) as source:
            raw = source.read(_agent._MAX + 1)
    except FileNotFoundError:
        return []
    state = _agent.decode(base64.b64encode(raw).decode('ascii'))
    if not isinstance(state, dict) or set(state) != {'version', 'messages'} or type(state['version']) is not int or state['version'] != 1:
        raise ValidationError('Saved assistant conversation has an unsupported format')
    text_messages(state['messages'])
    return state['messages']


# This step replays the broker's durable response. It never retries a provider API.
@step(name='nodus-assistant-answer', version='1', effect='pure')
def _answer(task):
    path = os.environ.get('NODUS_CHECKPOINT_DIR', '')
    if not path or not os.path.isabs(path):
        raise ValidationError('The assistant requires its assigned checkpoint directory')
    limit = os.environ.get('NODUS_AGENT_MODEL_MAX_OUTPUT_TOKENS', '')
    if not limit.isascii() or not limit.isdecimal():
        raise ValidationError('The assistant requires its accepted model output limit')
    with open_directory(Path(path)) as directory:
        messages = [*_conversation(directory), {'role': 'user', 'content': task}]
        response = _agent.model(messages, call_id='answer', max_output_tokens=int(limit))
        content = response.get('content')
        if (not isinstance(content, list) or not content
                or any(not isinstance(item, dict) or item.get('type') != 'text' or not isinstance(item.get('text'), str) for item in content)):
            raise StepOutcomeUnknown('The assistant requires a completed text response. Inspect the run before continuing')
        text = '\n'.join(item['text'] for item in content)
        if not text:
            raise StepOutcomeUnknown('The assistant received an empty response. Inspect the run before continuing')
        state = {'version': 1, 'messages': [*messages, {'role': 'assistant', 'content': text}]}
        _agent.encode(state)
        raw = json.dumps(state, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        with directory.stage() as output:
            output.write(raw)
            output.stream.flush()
            os.fsync(output.stream.fileno())
            output.commit(_STATE_FILE)
    return {'text': text, 'model': response['model'], 'stop_reason': response.get('stop_reason'),
            'usage': response.get('usage')}


def main(event):
    """Answer one task using the deployment's model and restored conversation."""
    if not isinstance(event, dict) or set(event) != {'task'} or not isinstance(event['task'], str) or not event['task'].strip():
        raise ValidationError('The assistant input must contain one nonempty task string')
    return _answer(event['task'], _step_id='assistant-answer')
