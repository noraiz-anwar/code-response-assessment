from .mixins import ScriptedLanguageExecutorMixin

from ..interface import CodeExecutor


class JavascriptCodeExecutor(ScriptedLanguageExecutorMixin, CodeExecutor):
    docker_image = 'litmustest/code-executor-node:18.12'
    language = 'javascript'
    version = 'nodejs-18.12'
    display_name = 'Javascript (NodeJS 18.12)'

    id = CodeExecutor.create_id('javascript', 'nodejs-18.12')

    SOURCE_FILE_NAME_TEMPLATE = '{name}.js'
    RUN_COMMAND_STDIN_INPUT_TEMPLATE = 'node {source_file}'
    RUN_COMMAND_FILE_INPUT_TEMPLATE = 'node {source_file} {input_file}'
