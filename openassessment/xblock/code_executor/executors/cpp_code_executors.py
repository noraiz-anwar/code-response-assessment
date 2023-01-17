from .mixins import CompiledLanguageExecutorMixin

from ..interface import CodeExecutor


class CppCodeExecutor(CompiledLanguageExecutorMixin, CodeExecutor):
    docker_image = 'litmustest/code-executor-gpp:12.2'
    language = 'cpp'
    version = 'g++-12.2'
    display_name = 'C++ 20 (g++ 12.2)'

    id = CodeExecutor.create_id('cpp', 'g++-12.2')

    SOURCE_FILE_NAME_TEMPLATE = '{name}.cpp'
    EXECUTABLE_FILE_NAME_TEMPLATE = '{name}.out'
    COMPILE_COMMAND_TEMPLATE = 'g++-12 -o {executable_file} -std=gnu++2a {source_file} $CPP_LD_FLAGS'
    RUN_COMMAND_STDIN_INPUT_TEMPLATE = './{executable_file}'
    RUN_COMMAND_FILE_INPUT_TEMPLATE = './{executable_file} {input_file}'
