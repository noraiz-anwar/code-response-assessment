"""
Holds utility functions related to code_grader module.
"""


from litmustest_djangoapps.core.models import Question


def is_design_problem(usage_id=None, problem_name=None, question=None):
    """
    Temporary helper method to check if a coding problem is a design problem.
    """
    if question:
        return question.sub_category == "desgin_problem"
    
    question = Question.get_by_usage_key(
    usage_key=str(usage_id),
    fallback_title=problem_name
    )
    problem_name_in_lower = problem_name.lower()
    return question.sub_category == "desgin_problem" or problem_name_in_lower.endswith('design problem')

def get_error_response(run_type, error, is_design_problem=False):
    """
    Create a sample error response for a given run and the error to be displayed.
    """
    return {
        'run_type': run_type,
        'total_tests': 0,
        'correct': 0,
        'incorrect': 0,
        'output': None,
        'error': [error],
        'is_design_problem': is_design_problem,
    }


def truncate_error_output(output):
    """
    Truncate error output to last 150 lines if it is very long.
    """
    if len(output.split('\n')) > 150:
        actual_output = output.split("\n")[-150:]
        actual_output.append("... Extra output Trimmed.")
        return "\n".join(actual_output)
    return output
