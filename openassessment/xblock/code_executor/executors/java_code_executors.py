from .mixins import CompiledLanguageExecutorMixin

from ..interface import CodeExecutor


class JavaCodeExecutor(CompiledLanguageExecutorMixin, CodeExecutor):
    docker_image = 'litmustest/code-executor-openjdk:19'
    language = 'java'
    version = 'openjdk-19'
    display_name = 'Java 19 (openjdk 19)'

    id = CodeExecutor.create_id('java', 'openjdk-19')

    SOURCE_FILE_NAME_TEMPLATE = 'Main.java'
    EXECUTABLE_FILE_NAME_TEMPLATE = 'Main'
    COMPILE_COMMAND_TEMPLATE = 'javac {source_file}'
    RUN_COMMAND_STDIN_INPUT_TEMPLATE = 'java {executable_file}'
    RUN_COMMAND_FILE_INPUT_TEMPLATE = 'java {executable_file} {input_file}'
