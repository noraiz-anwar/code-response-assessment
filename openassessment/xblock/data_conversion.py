"""
Data Conversion utility methods for handling ORA2 XBlock data transformations and validation.

"""
from __future__ import absolute_import

import json

import six

from openassessment.xblock.job_sample_grader.utils import get_error_response


def convert_training_examples_list_to_dict(examples_list):
    """
    Convert of options selected we store in the problem def,
    which is ordered, to the unordered dictionary of options
    selected that the student training API expects.

    Args:
        examples_list (list): A list of options selected against a rubric.

    Returns:
        A dictionary of the given examples in the list.

    Example:
        >>> examples = [
        >>>     {
        >>>         "answer": {
        >>>             "parts": {
        >>>                 [
        >>>                     {"text:" "Answer part 1"},
        >>>                     {"text:" "Answer part 2"},
        >>>                     {"text:" "Answer part 3"}
        >>>                 ]
        >>>             }
        >>>         },
        >>>         "options_selected": [
        >>>             {
        >>>                 "criterion": "Ideas",
        >>>                 "option": "Fair"
        >>>             },
        >>>             {
        >>>                 "criterion": "Content",
        >>>                 "option": "Good"
        >>>             }
        >>>         ]
        >>>     }
        >>> ]
        >>> convert_training_examples_list_to_dict(examples)
        [
            {
                'answer': {
                    'parts': {
                        [
                            {'text:' 'Answer part 1'},
                            {'text:' 'Answer part 2'},
                            {'text:' 'Answer part 3'}
                        ]
                    }
                 },
                'options_selected': {
                    'Ideas': 'Fair',
                    'Content': 'Good'
                }
            }
        ]

    """
    return [
        {
            'answer': ex['answer'],
            'options_selected': {
                select_dict['criterion']: select_dict['option']
                for select_dict in ex['options_selected']
            }
        }
        for ex in examples_list
    ]


def update_assessments_format(assessments):
    """
    For each example update 'answer' to newer format.

    Args:
        assessments (list): list of assessments
    Returns:
        list of dict
    """
    for assessment in assessments:
        if 'examples' in assessment and assessment['examples']:
            for example in assessment['examples']:
                if (isinstance(example, dict) and
                    (isinstance(example['answer'], six.text_type) or isinstance(example['answer'], str))):
                    example['answer'] = {
                        'parts': [
                            {'text': example['answer']}
                        ]
                    }
                if isinstance(example, dict) and isinstance(example['answer'], list) and example['answer']:
                    example['answer'] = {
                        'parts': [
                            {'text': example_answer} for example_answer in example['answer']
                        ]
                    }
    return assessments


def create_prompts_list(prompt_or_serialized_prompts):
    """
    Construct a list of prompts.

    Initially a block had a single prompt which was saved as a simple string.
    In that case a new prompt dict is constructed from it.

    Args:
        prompt_or_serialized_prompts (unicode): A string which can either
        be a single prompt text or json for a list of prompts.

    Returns:
        list of dict
    """

    if prompt_or_serialized_prompts is None:
        prompt_or_serialized_prompts = ''

    try:
        prompts = json.loads(prompt_or_serialized_prompts)
    except ValueError:
        prompts = [
            {
                'description': prompt_or_serialized_prompts,
            }
        ]
    return prompts


def create_rubric_dict(prompts, criteria):
    """
    Construct a serialized rubric model in the format expected
    by the assessments app.

    Args:
        prompts (list of dict): The rubric prompts.
        criteria (list of dict): The serialized rubric criteria.

    Returns:
        dict

    """
    return {
        "prompts" : prompts,
        "criteria": criteria
    }


def clean_criterion_feedback(rubric_criteria, criterion_feedback):
    """
    Remove per-criterion feedback for criteria with feedback disabled
    in the rubric.

    Args:
        rubric_criteria (list): The rubric criteria from the problem definition.
        criterion_feedback (dict): Mapping of criterion names to feedback text.

    Returns:
        dict

    """
    return {
        criterion['name']: criterion_feedback[criterion['name']]
        for criterion in rubric_criteria
        if criterion['name'] in criterion_feedback
        and criterion.get('feedback', 'disabled') in ['optional', 'required']
    }


def prepare_submission_for_serialization(submission_data):
    """
    Convert a list of answers into the right format dict for serialization.

    Args:
        submission_data (list of unicode): The answers.

    Returns:
        dict
    """
    return {
        'parts': [{'text': text} for text in submission_data],
    }


def prepare_submission_for_serialization_v2(submission_data):
    """
    Convert the list to indexed dict for saving submission.
    """
    sub_dict = {}
    for index, value in enumerate(submission_data):
        sub_dict[index] = value
    return sub_dict


def create_submission_dict(submission, prompts, staff_view=False):
    """
    1. Convert from legacy format.
    2. Add prompts to submission['answer']['parts'] to simplify iteration in the template.

    Args:
        submission (dict): Submission dictionary.
        prompts (list of dict): The prompts from the problem definition.
        staff_view: If staff is viewing submission, then add the staff test cases output

    Returns:
        dict
    """

    parts = [{'prompt': prompt, 'text': ''} for prompt in prompts]
    if staff_view:
        parts.append({'prompt': {'description': "Staff Test Cases Output"}, 'text': ''})
        parts.append({'prompt': {'description': "Staff Test Cases Expected"}, 'text': ''})

    if 'text' in submission['answer']:
        parts[0]['text'] = submission['answer'].pop('text')
    else:
        for index, part in enumerate(submission['answer'].pop('parts')):
            try:
                parts[index]['text'] = part['text']
            except IndexError:
                # To avoid showing the staff output to learner when they have submitted their submission

                # This error is raised as staff output is saved without any prompt and when trying to change
                # the submission format, it tries to add the staff output but since no prompt is set
                # at that index, it raises IndexError
                continue
    submission['answer']['parts'] = parts

    return submission


def create_submission_dict_v2(submission, prompts, staff_view=False):
    if not staff_view:
        # Delete staff info from the submission dict if not required
        try:
            del submission['answer']['2']
        except Exception:
            pass
    submission['answer']['parts'] = [submission['answer']['0'], submission['answer']['1']]
    if staff_view:
        try:
            submission['answer']['parts'].append(submission['answer']['2'])
        except KeyError:
            submission['answer']['parts'].append(
                get_error_response('staff', "Missing Staff Submission")
                )

    return submission


def update_submission_old_format_answer(submission):
    """
    Update the submission answer from indexed-key format to new format, the format that uses
    semantically correct keys
    """
    answer = submission['answer']

    if '0' in answer and '1' in answer:
        new_answer = {'submission': answer['0'], 'sample_run': answer['1']}

        try:
            new_answer.update({'staff_run': answer['2']})
        except KeyError:
            new_answer.update({
                'staff_run': get_error_response('staff', "Missing Staff Submission")
                })

        try:
            new_answer.update({'language': answer['0'].split('\n')[0]})
        except Exception:
            new_answer.update({'language': None})

        submission['answer'] = new_answer

    return submission


def make_django_template_key(key):
    """
    Django templates access dictionary items using dot notation,
    which means that dictionary keys with hyphens don't work.
    This function sanitizes a key for use in Django templates
    by replacing hyphens with underscores.

    Args:
        key (basestring): The key to sanitize.

    Returns:
        basestring
    """
    return key.replace('-', '_')


def verify_assessment_parameters(func):
    """
    Verify that the wrapped function receives the given parameters.

    Used for the staff_assess, self_assess, peer_assess functions and uses their data types.

    Args:
        func - the function to be modified

    Returns:
        the modified function
    """
    def verify_and_call(instance, data, suffix):
        # Validate the request
        if 'options_selected' not in data:
            return {'success': False, 'msg': instance._('You must provide options selected in the assessment.')}

        if 'overall_feedback' not in data:
            return {'success': False, 'msg': instance._('You must provide overall feedback in the assessment.')}

        if 'criterion_feedback' not in data:
            return {'success': False, 'msg': instance._('You must provide feedback for criteria in the assessment.')}

        return func(instance, data, suffix)
    return verify_and_call
