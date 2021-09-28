import glob
import logging
import os
import re
import shutil
import subprocess
import uuid

from collections import OrderedDict
from openassessment.xblock.job_sample_grader.utils import (
    is_design_problem, get_error_response, truncate_error_output)


logger = logging.getLogger(__name__)


class CodeGraderMixin(object):

    ALLOWED_LANGUAGES = ['python', 'java', 'c++']

    # the extensions of language-specific code files
    LANGUAGE_EXTENSION_MAP = {
        'python': 'py',
        'java': 'java',
        'c++': 'cpp'
    }
    __SECRET_DATA_DIR__ = "/grader_data/"
    __TMP_DATA_DIR__ = "/tmp/"

    def grade(self, response, add_staff_cases=False):
        """
        Setting prerequiste and execute code.
            * Submission code is added in directory
            * File name is auto generated unique string
            * For designed problem simply code executes but for other problems code executed with
                test cases.
        Args:
            response (dict):
                problem_name(str)
                submission(str): code submitted by user
                language(str): language in which code is written
                add_staff_cases (bool, optional): Defaults to False.
        """
        problem_name = response['problem_name']
        source_code = response['submission']
        language = response.get('language')

        if not language or (language and language.lower() not in self.ALLOWED_LANGUAGES):
            return self.response_with_error_v2("Language can only be Python, Java, or C++")
        else:
            language = self.LANGUAGE_EXTENSION_MAP[language.lower()]

        code_file_name = "auto_generated_code_file_" + str(uuid.uuid4()).replace('-', '')
        if not os.path.exists(self.__TMP_DATA_DIR__ + code_file_name):
            os.mkdir(self.__TMP_DATA_DIR__ + code_file_name)

        code_file_path = self.__TMP_DATA_DIR__ + code_file_name + "/" + code_file_name

        try:
            if language.lower() == 'java':
                student_response = self.update_java_code(source_code, code_file_name)
            else:
                student_response = source_code
            lang_extension_file_path = '{0}.{1}'.format(code_file_path, language)
            self.write_code_file(student_response, lang_extension_file_path)
        except UnicodeEncodeError as e:
            return self.response_with_error_v2("{} - {} : {}".format(
                e.start, e.end, e.reason
            ))
        except Exception as exc:
            return self.response_with_error_v2(str(exc))

        output = []
        if is_design_problem(problem_name):
            output.append(self.run_design_code(language, code_file_name, lang_extension_file_path))
        else:
            sample_result = self.run_code('sample', language, code_file_name, lang_extension_file_path, problem_name)
            output.append(sample_result)
            if add_staff_cases:
                staff_result = self.run_code('staff', language, code_file_name, lang_extension_file_path, problem_name)
                output.append(staff_result)

        shutil.rmtree(self.__TMP_DATA_DIR__ + code_file_name)

        return output

    def response_with_error_v2(self, error):
        """
        To make the incorrect language error compatible with per file test
        case run compatible.
        """
        return [get_error_response('sample', error)]



    def run_code(self, run_type, lang, code_file_name, lang_extension_file_path, problem_name):
        """
        Executes learner code with test case files. Response is made on correctness.

        Args:
            run_type (string): For now staff or sample
            lang (string): Any language is language set
            code_file_name (str): Name of file
            lang_extension_file_path (str): complete path of the code file with proper lang extension
            problem_name (str)

        Returns(dict):
                Returns a dict with the following keys containing the result of code execution:
                    * run_type(str): Same as input
                    * total_tests(int): Total number of tests
                    * correct(int): Number of passed tests
                    * incorrect(int): Number of failed tests
                    * output(dict): Output of problem and test case
                    * error(str): any error occurred during the code execution
        """

        test_cases = glob.glob("{}{}/{}/*".format(self.__SECRET_DATA_DIR__, problem_name, run_type))

        # Sort the test cases based on the test number
        if test_cases:
            test_cases = sorted(test_cases, key=lambda test_case: int(test_case.split('/')[-1]))

        output = {
            'run_type': run_type,
            'total_tests': len(test_cases),
            'correct': 0,
            'incorrect': 0,
            'output': OrderedDict(),
            'error': None
        }
        for case in test_cases:
            case_number = int(case.split('/')[-1])
            input_file = "{}/input.in".format(case)
            expected_output_file = "{}/output.out".format(case)
            run_output = self.run_test_cases(
                lang, code_file_name,
                lang_extension_file_path,
                input_file,
                expected_output_file,
                timeout=5,
                problem_name=problem_name
            )
            # If execution faced error, stop processing
            if run_output['errors']:
                output['error'] = self.process_execution_error(run_output['errors'])
                break
            if run_output['correct']:
                output['correct'] += 1
            else:
                output['incorrect'] += 1
            expected_output = run_output['tests'][0][1]
            actual_output = run_output['tests'][0][2]
            test_input = open(input_file, 'r').read()
            output['output'][case_number] = {
                'test_input': test_input,
                'actual_output': actual_output,
                'expected_output': expected_output,
                'correct': run_output['correct']
            }

        return output

    def run_design_code(self, lang, code_file_name, lang_extension_file_path):
        """
        Method to run the design based problems i.e. problems with no test case files
        Args:
            lang(str): code language
            code_file_name(str): name of the code file
            lang_extension_file_path(str): complete path of the code file with proper lang extension

        Returns(dict):
                Returns a dict with the following keys containing the result of code execution:
                    * is_design_problem(str): Boolean to specify if the problem is design problem
                    * output(str): the code execution output
                    * error(str): any error occurred during the code execution
                    * run_type(str): defaults to sample
        """
        output = {
            'is_design_problem': True,
            'run_type': 'sample',
            'output': None,
            'error': None
        }
        try:
            output['output'] = self.execute_code(lang, code_file_name, lang_extension_file_path, None, timeout=15)
        except Exception as e:
            output['error'] = self.process_execution_error([str(e)])

        return output

    @classmethod
    def get_test_case_count(cls, problem_name, run_type):
        """
        Return the test case count of a given run type for a problem.

        Returns:
            Count of the test cases or None
        """
        test_cases = glob.glob("{}{}/{}/*".format(cls.__SECRET_DATA_DIR__, problem_name, run_type))
        return len(test_cases) if test_cases else None

    def run_as_subprocess(self, cmd, compiling=False, running_code=False, timeout=None):
        """
        runs the subprocess and execute the command. if timeout is given kills the
        process after the timeout period has passed. compiling and running code flags
        helps to return related message in exception
        """

        if timeout:
            cmd = 'timeout --signal=SIGKILL {0} {1}'.format(timeout, cmd)

        output, error = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
        ).communicate()
        error = error.decode('utf-8')
        if error and compiling:
            raise Exception(error)
        elif error and running_code and 'Killed' in error:
            return u'Time limit exceeded.'
        elif error and running_code:
            raise Exception(error)

        return output.decode('utf-8')

    def respond_with_error(self, message):
        """
        returns error response with message
        """
        return {
            'correct': False,
            'score': 0,
            'errors': [message],
            'tests': []
        }

    def execute_code(self, lang, code_file_name, lang_extension_file_path, input_file, timeout=10):
        """
        compiles the code, runs the code for python, java and c++ and returns output of the code.
        """
        if lang == 'py':
            return self.run_python_code(lang_extension_file_path, timeout, input_file)
        elif lang == 'java':
            return self.run_java_code(code_file_name, timeout, input_file)
        elif lang == 'cpp':
            return self.run_cpp_code(lang_extension_file_path, timeout, input_file)
        else:
            raise Exception

    def run_python_code(self, code_file, timeout, code_input_file=None):
        """
        Wrapper to run python code.
        Args:
            code_file(str): path to code file
            timeout(int): time after which the code execution will be forced-kill.
            code_input_file(str): Optional parameter, path to the input file that will be provided to code file.

        Returns:
            str output of the code execution
        """
        execution_command = 'python3 ' + code_file
        if code_input_file:
            execution_command += ' {}'.format(code_input_file)
        return self.run_as_subprocess(execution_command, running_code=True, timeout=timeout)

    def run_java_code(self, code_file_name, timeout, code_input_file=None):
        """
        Wrapper to run Java code.
        Args:
            code_file_name(str): name of the code file
            timeout(int): time after which the code execution will be forced-kill.
            code_input_file(str): Optional parameter, path to the input file that will be provided to code file.

        Returns:
            str output of the code execution
        """
        filename_with_lang_extension = "{}{}/{}.{}".format(
            self.__TMP_DATA_DIR__, code_file_name, code_file_name, 'java'
        )
        compilation_command = 'javac -cp {0} {1}'.format(
            self.__SECRET_DATA_DIR__ + "json-simple-1.1.1.jar", filename_with_lang_extension
        )
        execution_command = "java -cp {} {}".format(
            self.__TMP_DATA_DIR__ + code_file_name + ":" + self.__SECRET_DATA_DIR__ + "json-simple-1.1.1.jar",
            code_file_name
        )
        if code_input_file:
            execution_command += " {}".format(code_input_file)
        self.run_as_subprocess(compilation_command, compiling=True)
        return self.run_as_subprocess(execution_command, running_code=True, timeout=timeout)

    def run_cpp_code(self, code_file, timeout, code_input_file=None):
        """
        Wrapper to run C++ code.
        Args:
            code_file(str): path to code file
            timeout(int): time after which the code execution will be forced-kill.
            code_input_file(str): Optional parameter, path to the input file that will be provided to code file.

        Returns:
            str output of the code execution
        """
        compiled_file_path = code_file + '.o'
        if not compiled_file_path.startswith('/'):
            compiled_file_path = '/' + compiled_file_path

        compilation_command = 'g++ ' + code_file + ' -o ' + compiled_file_path + " -lcurl"
        self.run_as_subprocess(compilation_command, compiling=True)

        execution_command = compiled_file_path
        if code_input_file:
            execution_command += " {}".format(code_input_file)
        return self.run_as_subprocess(execution_command, running_code=True, timeout=timeout)

    def update_java_code(self, source_code, code_file_name):
        """
        Rewrite java code to have public class name replaced with the uuid generated name.
        """
        return re.sub(
            'public class (.*) {', 'public class {0} {{'.format(code_file_name), source_code
        )

    def write_code_file(self, source_code, full_code_file_name):
        """
        accepts code and file name to where the code will be written.
        """
        f = open(full_code_file_name, 'w')
        f.write(source_code)
        f.close()

    def compare_outputs(self, actual_output, expected_output_file, problem_name):
        """
        compares actual and expected output line by line after stripping
        any whitespaces at the ends. Raises Exception if outputs do not match
        otherwise returns response of correct answer
        Args:
            actual_output(str): output of learner code
            expected_output_file(str): file name containing expected output of test case
            problem_name(str)
        """

        if not is_design_problem(problem_name):
            expected_output = open(expected_output_file, 'r').read().rstrip()
            actual_output = actual_output.rstrip()

            expected_output_splited = expected_output.split('\n')
            actual_output_splited = actual_output.split('\n')

            if actual_output_splited != expected_output_splited:
                return {
                    'correct': False,
                    'score': 0,
                    'errors': [],
                    'tests': [[False, expected_output, actual_output]]
                }
            else:
                return {
                    'correct': True,
                    'score': 1,
                    'errors': [],
                    'tests': [[True, expected_output, actual_output]]
                }
        else:
            return {
                'correct': True,
                'score': 1,
                'errors': [],
                'tests': [[True, "", actual_output.strip()]]
            }

    def run_test_cases(self, lang, code_file_name, lang_extension_file_path, input_file_argument,
                       expected_output_file, timeout=10, problem_name=''):
        """
        Execute given code file and compare output with expected
        Args:
            lang(str): code language
            code_file_name(str): name of the code file
            lang_extension_file_path(str): complete path of the code file with proper lang extension
            input_file_argument(str): file name in which test case input is given
            expected_output_file: file name containing expected output of test case
            timeout(int)
            problem_name(str)
        output (test case output)
        """
        try:
            output = self.execute_code(lang, code_file_name, lang_extension_file_path, input_file_argument, timeout)
            result = self.compare_outputs(output, expected_output_file, problem_name)
            return result
        except Exception as e:
            return self.respond_with_error(str(e))

    def process_execution_error(self, error):
        """
        Helper method to process and extract the execution error
        """
        try:
            output_error = error[0]
        except IndexError:
            output_error = error
        return truncate_error_output(output_error)

    def grade_response(self, data, problem_name, add_staff_output=False):
        """
        Grade the response with per file test case feature.
        """
        data.update({'problem_name': problem_name})
        output = self.grade(data, add_staff_cases=add_staff_output)

        sample_output = output[0]
        if add_staff_output:
            # If staff output is required, send the original result as it is.
            return output
        return sample_output
