from __future__ import absolute_import

import json
import logging
import os
import simplejson
import six
import webob

from datetime import timedelta
from typing import Union

from django.db import transaction
from six.moves import range

from celery.result import states as celery_task_states
from celery.result import AsyncResult
from django.utils import timezone
from django.conf import settings

from openassessment.fileupload import api as file_upload_api
from openassessment.fileupload.exceptions import FileUploadError
from openassessment.workflow.errors import AssessmentWorkflowError
from openassessment.xblock.tasks import run_and_save_staff_test_cases, run_and_save_test_cases_output
from xblock.core import XBlock

from lms.djangoapps.courseware.models import StudentModule
from student.models import user_by_anonymous_id
from edx_proctoring.models import ProctoredExamStudentAttempt
from openassessment.xblock.data_conversion import update_submission_old_format_answer
from .job_sample_grader.utils import is_design_problem
from .resolve_dates import DISTANT_FUTURE
from .user_data import get_user_preferences
from .utils import get_code_language

logger = logging.getLogger(__name__)


class SubmissionMixin(object):
    """Submission Mixin introducing all Submission-related functionality.

    Submission Mixin contains all logic and handlers associated with rendering
    the submission section of the front end, as well as making all API calls to
    the middle tier for constructing new submissions, or fetching submissions.

    SubmissionMixin is a Mixin for the OpenAssessmentBlock. Functions in the
    SubmissionMixin call into the OpenAssessmentBlock functions and will not
    work outside the scope of OpenAssessmentBlock.

    """

    ALLOWED_IMAGE_MIME_TYPES = ['image/gif', 'image/jpeg', 'image/pjpeg', 'image/png']

    ALLOWED_FILE_MIME_TYPES = ['application/pdf'] + ALLOWED_IMAGE_MIME_TYPES

    MAX_FILES_COUNT = 20

    STAFF_EXPECTED = 'staff_expected'
    STAFF_OUTPUT = 'staff_out'
    SAMPLE_EXPECTED = 'sample_expected'
    SAMPLE_OUTPUT = 'sample_out'

    # taken from http://www.howtogeek.com/137270/50-file-extensions-that-are-potentially-dangerous-on-windows/
    # and http://pcsupport.about.com/od/tipstricks/a/execfileext.htm
    # left out .js and office extensions
    FILE_EXT_BLACK_LIST = [
        'exe', 'msi', 'app', 'dmg', 'com', 'pif', 'application', 'gadget',
        'msp', 'scr', 'hta', 'cpl', 'msc', 'jar', 'bat', 'cmd', 'vb', 'vbs',
        'jse', 'ws', 'wsf', 'wsc', 'wsh', 'scf', 'lnk', 'inf', 'reg', 'ps1',
        'ps1xml', 'ps2', 'ps2xml', 'psc1', 'psc2', 'msh', 'msh1', 'msh2', 'mshxml',
        'msh1xml', 'msh2xml', 'action', 'apk', 'app', 'bin', 'command', 'csh',
        'ins', 'inx', 'ipa', 'isu', 'job', 'mst', 'osx', 'out', 'paf', 'prg',
        'rgs', 'run', 'sct', 'shb', 'shs', 'u3p', 'vbscript', 'vbe', 'workflow',
        'htm', 'html',
    ]

    def get_user_id_from_student_dict(self, student_item_dict: dict) -> Union[int, None]:
        """
        Given a `student_item_dict` return the user id of the related user.

        Args:
            student_item_dict (dict): dict as returned by `self.get_student_item_dict`.

        Returns:
            Union[int, None]: Anonymous user id or user id (whatever is assosiated with this block).
        """
        anonymous_student_id = None
        anonymous_student = user_by_anonymous_id(student_item_dict.get('student_id'))
        if anonymous_student is not None:
            anonymous_student_id = anonymous_student.id

        return anonymous_student_id or student_item_dict.get('student_id')

    def submit_code_response(self, data: dict, student_item_dict: dict):
        """
        Create submission for the coding question.

        Args:
            data (dict): A dictionary with the following shape (example):
                {
                    'executor_id': 'server_shell-python:3.5.2',
                    'problem_name': 'Sample Coding Question 1',
                    'submission': "print('asd')"
                }
            student_item_dict (dict): A student info dict, with the following shape (example):
                {
                    'course_id': 'course-v1:litmustest+litmustest.LT629.1+1',
                    'item_id': 'block-v1:litmustest+litmustest.LT629.1+1+type@openassessment+block@c6855e4faae44ad7af2431489e3d3573',
                    'item_type': 'openassessment',
                    'student_id': 'ebf6f228c823c9138cd1cf1ff3504680'
                }
        Returns:
            dict: A submission info dict, with the following shape (example):
                {
                    'answer': {
                        'executor_id': 'server_shell-python:3.5.2',
                        'problem_name': 'Sample Coding Question 1',
                        'sample_run': {
                            'correct': 0,
                            'error': None,
                            'incorrect': 2,
                            'output': OrderedDict([(1,
                                                    {'actual_output': 'asd',
                                                        'correct': False,
                                                        'expected_output': 'NO',
                                                        'test_input': '1'}),
                                                    (2,
                                                    {'actual_output': 'asd',
                                                        'correct': False,
                                                        'expected_output': 'YES',
                                                        'test_input': '2'})]),
                            'run_type': 'sample',
                            'total_tests': 2},
                            'submission': "print('asd')"},
                    'attempt_number': 1,
                    'created_at': datetime.datetime(2022, 10, 7, 10, 45, 57, 491023, tzinfo=<UTC>),
                    'student_item': 2,
                    'submitted_at': datetime.datetime(2022, 10, 7, 10, 45, 57, 490999, tzinfo=<UTC>),
                    'team_submission_uuid': None,
                    'uuid': '432249dd-6431-4fa3-a8e2-ab2ba34c8c40'
                }
        """
        grade_output = self.grade_response(data, self.display_name, add_staff_output=False)

        student_sub_data = {
            **data,
            'sample_run': grade_output,
        }

        try:
            saved_files_descriptions = json.loads(self.saved_files_descriptions)
        except ValueError:
            saved_files_descriptions = None

        submission = self.create_submission(
            student_item_dict,
            student_sub_data,
            saved_files_descriptions
        )

        run_and_save_staff_test_cases.apply_async(args=[
            str(self.scope_ids.usage_id), submission["uuid"], self.display_name
        ], kwargs={
            'course_id': student_item_dict.get('course_id'),
            'user_id': self.get_user_id_from_student_dict(student_item_dict)
        })

        return submission

    @XBlock.json_handler
    def submit(self, data, suffix=''):  # pylint: disable=unused-argument
        """Place the submission text into Openassessment system

        Allows submission of new responses.  Performs basic workflow validation
        on any new submission to ensure it is acceptable to receive a new
        response at this time.

        Args:
            data (dict): Data may contain two attributes: submission and
                file_urls. submission is the response from the student which
                should be stored in the Open Assessment system. file_urls is the
                path to a related file for the submission. file_urls is optional.
            suffix (str): Not used in this handler.

        Returns:
            (tuple): Returns the status (boolean) of this request, the
                associated status tag (str), and status text (unicode).

        """
        # Import is placed here to avoid model import at project startup.
        from submissions import api
        if 'submission' not in data:
            return (
                False,
                'EBADARGS',
                self._(u'"submission" required to submit answer.')
            )

        # Short-circuit if no user is defined (as in Studio Preview mode)
        # Since students can't submit, they will never be able to progress in the workflow
        if self.in_studio_preview:
            return (
                False,
                'ENOPREVIEW',
                self._(u'To submit a response, view this component in Preview or Live mode.')
            )

        status = False
        status_tag = 'ENOMULTI'  # It is an error to submit multiple times for the same item
        status_text = self._(u'Multiple submissions are not allowed.')

        workflow = self.get_workflow_info()
        if not workflow:
            student_item_dict = self.get_student_item_dict()
            student_id = self.get_user_id_from_student_dict(student_item_dict)
            attempt = ProctoredExamStudentAttempt.objects.get(user__id=student_id,
                                                              proctored_exam__course_id=student_item_dict[
                                                                  'course_id'])
            if attempt and attempt.status == 'submitted':
                msg = (
                    u"Attempt already submitted "
                    u"a response for the user: {student_item}"
                ).format(student_item=student_item_dict)
                logger.exception(msg)
                status_tag = 'EUNKNOWN'
                status_text = self._(u'Please refresh to submit')
                return (
                    False,
                    status_tag,
                    status_text
                )
            try:
                submission = self.submit_code_response(data, student_item_dict)
            except api.SubmissionRequestError as err:

                # Handle the case of an answer that's too long as a special case,
                # so we can display a more specific error message.
                # Although we limit the number of characters the user can
                # enter on the client side, the submissions API uses the JSON-serialized
                # submission to calculate length.  If each character submitted
                # by the user takes more than 1 byte to encode (for example, double-escaped
                # newline characters or non-ASCII unicode), then the user might
                # exceed the limits set by the submissions API.  In that case,
                # we display an error message indicating that the answer is too long.
                answer_too_long = any(
                    "maximum answer size exceeded" in answer_err.lower()
                    for answer_err in err.field_errors.get('answer', [])
                )
                if answer_too_long:
                    status_tag = 'EANSWERLENGTH'
                else:
                    msg = (
                        u"The submissions API reported an invalid request error "
                        u"when submitting a response for the user: {student_item}"
                    ).format(student_item=student_item_dict)
                    logger.exception(msg)
                    status_tag = 'EBADFORM'
            except (api.SubmissionError, AssessmentWorkflowError):
                msg = (
                    u"An unknown error occurred while submitting "
                    u"a response for the user: {student_item}"
                ).format(student_item=student_item_dict)
                logger.exception(msg)
                status_tag = 'EUNKNOWN'
                status_text = self._(u'API returned unclassified exception.')
            else:
                status = True
                status_tag = submission.get('student_item')
                status_text = submission.get('attempt_number')

        return status, status_tag, status_text

    def add_output_to_submission(self, data, grade_output, sub_type='sample'):
        """
        Add the result of the code output to the submission.

        Arguments:
            data(dict): Contains the submission data(code in our case)
            grade_output(dict): result of the grader
            sub_type(str): str that tells which output is to be added.
                There are two submission output:
                 1. Sample/Public(default param)
                 2. Staff/Private
        Return:
            None
        """
        keys_to_add = [self.SAMPLE_OUTPUT, self.SAMPLE_EXPECTED]
        if sub_type == 'staff':
            keys_to_add = [self.STAFF_OUTPUT, self.STAFF_EXPECTED]
        for each in keys_to_add:
            try:
                data['submission'].append(grade_output[each])
            except KeyError:
                # If the keys aren't found, which happens if the code submission has faced
                # some errors, then add an empty string
                data['submission'].append('')

    @XBlock.json_handler
    def auto_save_submission(self, data, suffix=''):
        """
        Save the current student's response submission without executing the code.
        If the student already has a response saved, this will overwrite it.

        Args:
            data (dict): Data should have a single key 'submission' that contains
                the text of the student's response. Optionally, the data could
                have a 'file_urls' key that is the path to an associated file for
                this submission.
            suffix (str): Not used.

        Returns:
            dict: Contains a bool 'success' and unicode string 'msg'.
        """
        if 'submission' in data:
            student_sub_data = data
            try:
                self.saved_response = json.dumps(student_sub_data)
                self.has_saved = True

                # Emit analytics event...
                self.runtime.publish(
                    self,
                    "openassessmentblock.save_submission",
                    {"saved_response": self.saved_response}
                )

            except:
                return {'success': False, 'msg': self._(u"This response could not be saved.")}
            else:
                return {
                    'success': True,
                    'msg': u'Auto Save Successful',
                }
        else:
            return {'success': False, 'msg': self._(u"This response could not be saved.")}

    def get_module_state_object(self, user_id: int) -> Union[StudentModule, None]:
        """
        Returns StudentModule object of this block for user `user_id`.

        Args:
            user_id (int): User id.

        Returns:
            Union[StudentModule, None]: StudentModule object for `user_id`.
        """
        module_state = StudentModule.objects.filter(
            module_state_key=str(self.scope_ids.usage_id),
            student=user_id,
            module_type='openassessment',
        ).first()

        return module_state

    def get_code_execution_results(self, user_id: int) -> dict:
        """
        Returns the code execution results by fetching it from
        StudentModule state. Results are parsed into a dict.

        Args:
            user_id (int): User id.

        Returns:
            dict: Results dict. Either an empty dict or a dict with the
                following format:
                {
                    "output": {
                        "private": null,
                        "public": {
                            "output": {
                                "1": {
                                    "test_input": "2",
                                    "expected_output": "YES",
                                    "correct": false,
                                    "actual_output": "Time limit exceeded."
                                },
                            },
                            "incorrect": 2,
                            "error": None,
                            "total_tests": 2,
                            "correct": 0
                        }
                    },
                    "message": "",
                    "success": True,
                }
        """
        state = simplejson.loads(self.get_module_state_object(user_id).state)
        results = state.get('code_execution_results', '{}') or '{}'
        return simplejson.loads(results)

    def set_code_execution_results(self, results: dict, user_id: int):
        """
        Adds results to the StudentModule state.

        Args:
            results (dict): A dict of results. See `get_code_execution_results` for format details.
            user_id (int): User id.
        """
        module_state_object = self.get_module_state_object(user_id)
        module_state = simplejson.loads(module_state_object.state)
        module_state['code_execution_results'] = simplejson.dumps(results)
        module_state_object.state = simplejson.dumps(module_state)
        module_state_object.save()

    def is_code_execution_in_progress(self) -> bool:
        """
        Whether or not current task `self.code_execution_task_id` is in a "running" state.

        Returns:
            bool: True if code is being executed.
        """
        if settings.CELERY_ALWAYS_EAGER:
            return False
        current_code_execution_task_state = self.get_current_code_execution_task_state()
        # celery_task_states.PENDING is an "unknown" state. Celery returns PENDING state for tasks
        # that don't even exist. So we need to handle them appropriately.
        if (
                current_code_execution_task_state not in celery_task_states.READY_STATES
                and current_code_execution_task_state != celery_task_states.PENDING
                or
                # A precausion against failed task registerations. If a request stays "PENDING" for 10 minutes,
                # we'll assume it's lost.
                # TODO: Remove this condition if we do not experience any such loses.
                current_code_execution_task_state == celery_task_states.PENDING
                and self.last_code_execution_attempt_date_time is not None
                and self.last_code_execution_attempt_date_time > (timezone.now() - timedelta(minutes=10))
        ):
            return True

        return False

    def get_current_code_execution_task_state(self) -> str:
        """
        Returns the celery task state of `self.code_execution_task_id` if exists.
        Defaults to SUCCESS.

        Returns:
            str: A celery task state.
        """
        current_code_execution_task_state = celery_task_states.SUCCESS

        if bool(self.code_execution_task_id) and not settings.CELERY_ALWAYS_EAGER:
            current_code_execution_task_state = AsyncResult(self.code_execution_task_id).state

        return current_code_execution_task_state

    def revoke_existing_task(self):
        """
        Revokes `self.code_execution_task_id` if it exists.
        """
        if bool(self.code_execution_task_id) and not settings.CELERY_ALWAYS_EAGER:
            task_result = AsyncResult(self.code_execution_task_id)
            if task_result.state not in celery_task_states.READY_STATES:
                task_result.revoke()
        self.code_execution_task_id = None
        self.save()

    @XBlock.json_handler
    def save_submission(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Save the current student's response submission and start code execution.
        If the student already has a response saved, this will overwrite it.

        Args:
            data (dict): Data should have a single key 'submission' that contains
                the text of the student's response. Optionally, the data could
                have a 'file_urls' key that is the path to an associated file for
                this submission.
            suffix (str): Not used.

        Returns:
            dict: Contains a bool 'success' and unicode string 'msg'.
        """
        if os.environ.get('SERVICE_VARIANT', '').lower() == 'cms':
            # CMS lacks a student entity and no StudentModule state
            # is saved. Async code execution requires this state as
            # an intermediate space.
            return {
                'success': False,
                'msg': self._(u'Code execution only works on lms.'),
            }

        if 'submission' not in data:
            return {
                'success': False,
                'msg': self._(u'This response was not submitted.')
            }

        if self.is_code_execution_in_progress():
            return {
                'success': False,
                'msg': self._(u'An existing code execution task is already running.')
            }

        # Ensure no task is running. We do not want to run multiple code execution tasks
        # at once.
        self.revoke_existing_task()

        show_staff_cases = self.show_private_test_case_results and not is_design_problem(self.scope_ids.usage_id,
                                                                                         self.display_name)

        self.saved_response = json.dumps(data)
        self.has_saved = True
        self.last_code_execution_attempt_date_time = timezone.now()
        self.code_execution_results = ''

        student_item_dict = self.get_student_item_dict()
        student_user_id = self.get_user_id_from_student_dict(student_item_dict)

        self.save()

        def run_code():
            self.code_execution_task_id = run_and_save_test_cases_output.apply_async(
                kwargs={
                    'block_id': str(self.scope_ids.usage_id),
                    'user_id': student_user_id,
                    'saved_response': data,
                    'add_staff_cases': show_staff_cases,
                }).task_id
            self.save()

        transaction.on_commit(run_code)

        # Emit analytics event...
        self.runtime.publish(
            self,
            "openassessmentblock.save_submission",
            {"saved_response": self.saved_response}
        )

        return {
            'success': True,
            'msg': u'Execution task started.',
        }

    @XBlock.handler
    def fetch_code_execution_results(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        Returns code execution results.

        Args:
            request (Any): Not used.
            suffix (Any): Not used. Defaults to ''.

        Returns:
            Response: A JSON response of shape (example):
                {
                    "output": {
                        "private": null,
                        "public": {
                            "output": {
                                "1": {
                                    "test_input": "2",
                                    "expected_output": "YES",
                                    "correct": false,
                                    "actual_output": "Time limit exceeded."
                                },
                            },
                            "incorrect": 2,
                            "error": null,
                            "total_tests": 2,
                            "correct": 0
                        }
                    },
                    "message": "",
                    "success": true,
                    "execution_state": "success" // Can be 'success', 'failure', 'running'
                }
        """
        execution_state = 'none'
        execution_results = {}
        show_staff_cases = self.show_private_test_case_results and not is_design_problem(self.scope_ids.usage_id,
                                                                                         self.display_name)

        if self.is_code_execution_in_progress():
            execution_state = 'running'
        elif self.get_current_code_execution_task_state() != celery_task_states.SUCCESS:
            execution_state = 'failure'
        else:
            execution_state = 'success'

        sample_output = {}
        staff_output = None
        execution_results = {}

        student_item_dict = self.get_student_item_dict()
        student_user_id = self.get_user_id_from_student_dict(student_item_dict)
        execution_results = self.get_code_execution_results(student_user_id) or {}

        output = execution_results.get('output', {}) or {}
        sample_output = output.get('sample')
        staff_output = output.get('staff')

        # Clean response. There are some values we don't want the user to see
        # on the frontend.
        if sample_output is not None:
            sample_output.pop('run_type', None)
        if staff_output is not None:
            staff_output.pop('run_type', None)
            if not show_staff_cases:
                staff_output.pop('output', None)
                staff_output.pop('error', None)

        return webob.Response(
            body=simplejson.dumps({
                'execution_state': execution_state,
                'success': execution_results.get('success', False),
                'message': execution_results.get('message', ''),
                'output': {
                    'public': sample_output,
                    'private': staff_output,
                },
            }),
            content_type='application/json',
            charset='utf8'
        )

    @XBlock.json_handler
    def save_files_descriptions(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Save the descriptions for each uploaded file.

        Args:
            data (dict): Data should have a single key 'descriptions' that contains
                the texts for each uploaded file.
            suffix (str): Not used.

        Returns:
            dict: Contains a bool 'success' and unicode string 'msg'.
        """
        if 'descriptions' in data:
            descriptions = data['descriptions']

            if isinstance(descriptions, list) and all(
                    [isinstance(description, six.string_types) for description in descriptions]):
                try:
                    self.saved_files_descriptions = json.dumps(descriptions)

                    # Emit analytics event...
                    self.runtime.publish(
                        self,
                        "openassessmentblock.save_files_descriptions",
                        {"saved_response": self.saved_files_descriptions}
                    )
                except:
                    return {'success': False, 'msg': self._(u"Files descriptions could not be saved.")}
                else:
                    return {'success': True, 'msg': u''}

        return {'success': False, 'msg': self._(u"Files descriptions were not submitted.")}

    def create_submission(self, student_item_dict, student_sub_data, files_descriptions=None):
        # Import is placed here to avoid model import at project startup.
        from submissions import api

        # Store the student's response text in a JSON-encodable dict
        # so that later we can add additional response fields.
        files_descriptions = files_descriptions if files_descriptions else []
        student_sub_dict = student_sub_data

        if self.file_upload_type:
            student_sub_dict['file_keys'] = []
            student_sub_dict['files_descriptions'] = []
            for i in range(self.MAX_FILES_COUNT):
                key_to_save = ''
                file_description = ''
                item_key = self._get_student_item_key(i)
                try:
                    url = file_upload_api.get_download_url(item_key)
                    if url:
                        key_to_save = item_key
                        try:
                            file_description = files_descriptions[i]
                        except IndexError:
                            pass
                except FileUploadError:
                    logger.exception(
                        u"FileUploadError for student_item: {student_item_dict}"
                        u" and submission data: {student_sub_data} with file"
                        "descriptions {files_descriptions}".format(
                            student_item_dict=student_item_dict,
                            student_sub_data=student_sub_data,
                            files_descriptions=files_descriptions
                        )
                    )
                if key_to_save:
                    student_sub_dict['file_keys'].append(key_to_save)
                    student_sub_dict['files_descriptions'].append(file_description)
                else:
                    break

        submission = api.create_submission(student_item_dict, student_sub_dict)
        self.create_workflow(submission["uuid"])
        self.submission_uuid = submission["uuid"]

        # Emit analytics event...
        self.runtime.publish(
            self,
            "openassessmentblock.create_submission",
            {
                "submission_uuid": submission["uuid"],
                "attempt_number": submission["attempt_number"],
                "created_at": submission["created_at"],
                "submitted_at": submission["submitted_at"],
                "answer": submission["answer"],
            }
        )

        return submission

    @XBlock.json_handler
    def upload_url(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Request a URL to be used for uploading content related to this
        submission.

        Returns:
            A URL to be used to upload content associated with this submission.

        """
        if 'contentType' not in data or 'filename' not in data:
            return {'success': False, 'msg': self._(u"There was an error uploading your file.")}
        content_type = data['contentType']
        file_name = data['filename']
        file_name_parts = file_name.split('.')
        file_num = int(data.get('filenum', 0))
        file_ext = file_name_parts[-1] if len(file_name_parts) > 1 else None

        if self.file_upload_type == 'image' and content_type not in self.ALLOWED_IMAGE_MIME_TYPES:
            return {'success': False, 'msg': self._(u"Content type must be GIF, PNG or JPG.")}

        if self.file_upload_type == 'pdf-and-image' and content_type not in self.ALLOWED_FILE_MIME_TYPES:
            return {'success': False, 'msg': self._(u"Content type must be PDF, GIF, PNG or JPG.")}

        if self.file_upload_type == 'custom' and file_ext.lower() not in self.white_listed_file_types:
            return {'success': False, 'msg': self._(u"File type must be one of the following types: {}").format(
                ', '.join(self.white_listed_file_types))}

        if file_ext in self.FILE_EXT_BLACK_LIST:
            return {'success': False, 'msg': self._(u"File type is not allowed.")}
        try:
            key = self._get_student_item_key(file_num)
            url = file_upload_api.get_upload_url(key, content_type)
            return {'success': True, 'url': url}
        except FileUploadError:
            logger.exception("FileUploadError:Error retrieving upload URL for the data:{data}.".format(data=data))
            return {'success': False, 'msg': self._(u"Error retrieving upload URL.")}

    @XBlock.json_handler
    def download_url(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Request a download URL.

        Returns:
            A URL to be used for downloading content related to the submission.

        """
        file_num = int(data.get('filenum', 0))
        return {'success': True, 'url': self._get_download_url(file_num)}

    @XBlock.json_handler
    def remove_all_uploaded_files(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Removes all uploaded user files.

        """
        removed_num = 0
        for i in range(self.MAX_FILES_COUNT):
            removed = file_upload_api.remove_file(self._get_student_item_key(i))
            if removed:
                removed_num += 1
            else:
                break
        return {'success': True, 'removed_num': removed_num}

    def _get_download_url(self, file_num=0):
        """
        Internal function for retrieving the download url.

        """
        try:
            return file_upload_api.get_download_url(self._get_student_item_key(file_num))
        except FileUploadError:
            logger.exception("Error retrieving download URL.")
            return ''

    def _get_student_item_key(self, num=0):
        """
        Simple utility method to generate a common file upload key based on
        the student item.

        Returns:
            A string representation of the key.

        """
        student_item_dict = self.get_student_item_dict()
        num = int(num)
        if num > 0:
            student_item_dict['num'] = num
            return u"{student_id}/{course_id}/{item_id}/{num}".format(
                **student_item_dict
            )
        else:
            return u"{student_id}/{course_id}/{item_id}".format(
                **student_item_dict
            )

    def _get_url_by_file_key(self, key):
        """
        Return download url for some particular file key.

        """
        url = ''
        try:
            if key:
                url = file_upload_api.get_download_url(key)
        except FileUploadError:
            logger.exception("Unable to generate download url for file key {}".format(key))
        return url

    def get_download_urls_from_submission(self, submission):
        """
        Returns a download URLs for retrieving content within a submission.

        Args:
            submission (dict): Dictionary containing an answer and a file_keys.
                The file_keys is used to try and retrieve a download urls
                with related content

        Returns:
            List with URLs to related content. If there is no content related to this
            key, or if there is no key for the submission, returns an empty
            list.

        """
        urls = []
        if 'file_keys' in submission['answer']:
            file_keys = submission['answer'].get('file_keys', [])
            descriptions = submission['answer'].get('files_descriptions', [])
            for idx, key in enumerate(file_keys):
                file_download_url = self._get_url_by_file_key(key)
                if file_download_url:
                    file_description = descriptions[idx].strip() if idx < len(descriptions) else ''
                    urls.append((file_download_url, file_description))
                else:
                    break
        elif 'file_key' in submission['answer']:
            key = submission['answer'].get('file_key', '')
            file_download_url = self._get_url_by_file_key(key)
            if file_download_url:
                urls.append((file_download_url, ''))
        return urls

    @staticmethod
    def get_user_submission(submission_uuid):
        """Return the most recent submission by user in workflow

        Return the most recent submission.  If no submission is available,
        return None. All submissions are preserved, but only the most recent
        will be returned in this function, since the active workflow will only
        be concerned with the most recent submission.

        Args:
            submission_uuid (str): The uuid for the submission to retrieve.

        Returns:
            (dict): A dictionary representation of a submission to render to
                the front end.

        """
        # Import is placed here to avoid model import at project startup.
        from submissions import api
        try:
            return api.get_submission(submission_uuid)
        except api.SubmissionRequestError:
            # This error is actually ok.
            return None

    @property
    def save_status(self):
        """
        Return a string indicating whether the response has been saved.

        Returns:
            unicode
        """
        return self._(u'This response has been saved but not submitted.') if self.has_saved else self._(
            u'This response has not been saved.')

    @XBlock.handler
    def render_submission(self, data, suffix=''):  # pylint: disable=unused-argument
        """Renders the Submission HTML section of the XBlock

        Generates the submission HTML for the first section of an Open
        Assessment XBlock. See OpenAssessmentBlock.render_assessment() for
        more information on rendering XBlock sections.

        Needs to support the following scenarios:
        Unanswered and Open
        Unanswered and Closed
        Saved
        Saved and Closed
        Submitted
        Submitted and Closed
        Submitted, waiting assessment
        Submitted and graded

        """
        path, context = self.submission_path_and_context()
        return self.render_assessment(path, context_dict=context)

    def submission_path_and_context(self):
        """
        Determine the template path and context to use when
        rendering the response (submission) step.

        Returns:
            tuple of `(path, context)`, where `path` (str) is the path to the template,
            and `context` (dict) is the template context.

        """
        workflow = self.get_workflow_info()
        problem_closed, reason, start_date, due_date = self.is_closed('submission')
        user_preferences = get_user_preferences(self.runtime.service(self, 'user'))

        path = 'openassessmentblock/response/oa_response.html'
        context = {
            **self.get_code_grader_context(),
            'user_timezone': user_preferences['user_timezone'],
            'user_language': user_preferences['user_language'],
            "xblock_id": self.get_xblock_id(),
            "text_response": self.text_response,
            "show_file_read_code": self.show_file_read_code,
            "is_code_input_from_file": self.is_code_input_from_file,
            "file_upload_response": self.file_upload_response,
            "prompts_type": self.prompts_type,
        }

        # Due dates can default to the distant future, in which case
        # there's effectively no due date.
        # If we don't add the date to the context, the template won't display it.
        if due_date < DISTANT_FUTURE:
            context["submission_due"] = due_date

        context['file_upload_type'] = self.file_upload_type
        context['allow_latex'] = self.allow_latex

        file_urls = None

        if self.file_upload_type:
            try:
                saved_files_descriptions = json.loads(self.saved_files_descriptions)
            except ValueError:
                saved_files_descriptions = []

            file_urls = []

            for i in range(self.MAX_FILES_COUNT):
                file_url = self._get_download_url(i)
                file_description = ''
                if file_url:
                    try:
                        file_description = saved_files_descriptions[i]
                    except IndexError:
                        pass
                    file_urls.append((file_url, file_description))
                else:
                    break
            context['file_urls'] = file_urls
        if self.file_upload_type == 'custom':
            context['white_listed_file_types'] = self.white_listed_file_types

        if not workflow and problem_closed:
            if reason == 'due':
                path = 'openassessmentblock/response/oa_response_closed.html'
            elif reason == 'start':
                context['submission_start'] = start_date
                path = 'openassessmentblock/response/oa_response_unavailable.html'
        elif not workflow:
            # For backwards compatibility. Initially, problems had only one prompt
            # and a string answer. We convert it to the appropriate dict.
            try:
                json.loads(self.saved_response)
                saved_response = {
                    'answer': json.loads(self.saved_response),
                }
            except ValueError:
                saved_response = {
                    'answer': {
                        'text': self.saved_response,
                    },
                }

            context['saved_response'] = saved_response['answer']
            context['save_status'] = self.save_status
            context['has_executed_code_before'] = self.last_code_execution_attempt_date_time != None

            submit_enabled = True
            if self.text_response == 'required' and not self.saved_response:
                submit_enabled = False
            if self.file_upload_response == 'required' and not file_urls:
                submit_enabled = False
            if self.text_response == 'optional' and self.file_upload_response == 'optional' \
                    and not self.saved_response and not file_urls:
                submit_enabled = False
            context['submit_enabled'] = submit_enabled
            path = "openassessmentblock/response/oa_response.html"
        elif workflow["status"] == "cancelled":
            context["workflow_cancellation"] = self.get_workflow_cancellation_info(self.submission_uuid)
            context["student_submission"] = self.get_user_submission(
                workflow["submission_uuid"]
            )
            path = 'openassessmentblock/response/oa_response_cancelled.html'
        elif workflow["status"] == "done":
            student_submission = self.get_user_submission(
                workflow["submission_uuid"]
            )
            context["student_submission"] = update_submission_old_format_answer(student_submission)
            context["design_problem"] = is_design_problem(self.scope_ids.usage_id, self.display_name)
            context['code_language'] = get_code_language(context["student_submission"]['answer']['executor_id'])
            path = 'openassessmentblock/response/oa_response_graded.html'
        else:
            student_submission = self.get_user_submission(
                workflow["submission_uuid"]
            )
            peer_in_workflow = "peer" in workflow["status_details"]
            self_in_workflow = "self" in workflow["status_details"]
            context["peer_incomplete"] = peer_in_workflow and not workflow["status_details"]["peer"]["complete"]
            context["self_incomplete"] = self_in_workflow and not workflow["status_details"]["self"]["complete"]

            context["design_problem"] = is_design_problem(self.scope_ids.usage_id, self.display_name)
            context["student_submission"] = update_submission_old_format_answer(student_submission)
            context['code_language'] = get_code_language(context["student_submission"]['answer']['executor_id'])

            path = 'openassessmentblock/response/oa_response_submitted.html'

        return path, context
