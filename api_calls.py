import flask
from flask import request, Blueprint, jsonify, make_response

import errors
import models
import schema
from datetime import datetime
import decorators


api_calls = Blueprint('api_calls', __name__)


def generate_and_update_user_session_key(user):
    import hashlib

    user_session_key = hashlib.sha512("{}/{}".format(user.id, datetime.utcnow()).encode("UTF-8")).hexdigest()

    session_key = "{}:{}:{}".format("sess", user.id, user_session_key)

    return session_key


@api_calls.route('/courses/<int:course_id>', methods=['GET'])
@api_calls.route('/courses', methods=['GET', 'POST'])
@decorators.check_session_role(return_user=True)
def course_api(current_user, course_id=None):
    """ Get a course or all courses"""
    if request.method == 'GET':

        if course_id:
            return schema.CourseSchema(many=False).dump(models.Course.get_by_id(course_id))

        return schema.CourseSchema(many=True).dumps(models.Course.get_all(), indent=4)

    if request.method == 'POST':
        """ Create a course"""

        validated_data = schema.CourseRequestSchema().load(flask.request.json or {})

        validated_data['teacher_id'] = current_user.id

        # Create course
        course = models.Course.create(**validated_data)

        return schema.CourseSchema(many=False).dump(course)


@api_calls.route('/users/<int:user_id>', methods=['GET'])
@api_calls.route('/users', methods=['GET'])
def user_api(user_id=None):
    """ Get a user or all users by role"""

    # Get user role param
    validated_data = schema.RoleRequestSchema().load(flask.request.args or {})

    if user_id:
        return schema.UserSchema(many=False).dump(models.User.get_by_id(user_id))

    all_users = models.User.get_all_by_role(role=validated_data.get('role'))
    if not validated_data.get('role'):
        flask.abort(make_response(jsonify(errors=errors.ERR_ROLE_PARAM_REQUIRED), 400))

    return schema.UserSchema(many=True).dumps(all_users, indent=4)


@api_calls.route('/login', methods=['POST'])
def login_api():
    """ Login for users"""

    validated_data = schema.UserLoginRequestSchema().load(flask.request.json or {})

    # Get user data
    user = models.User.get_by_email_and_password(
        email=validated_data.get('email'),
        password=validated_data.get('password')
    )

    if not user:
        flask.abort(make_response(jsonify(errors=errors.ERR_BAD_CREDENTIALS), 400))

    user_schema = schema.UserSchema(many=False).dump(user)

    # Generate session-id and save into db
    user_schema['session-id'] = generate_and_update_user_session_key(user=user)
    models.UserSession.create(**{'user_id': user.id, 'session_id': user_schema['session-id']})

    return user_schema


@api_calls.route('/user/session', methods=['GET'])
def session_api():
    """ Get session for users"""

    session_id = flask.request.headers.get('session-id')
    user_session = models.UserSession \
        .get_by_session_id(session_id=session_id)
    if user_session:
        user_schema = schema.UserSchema(many=False).dump(user_session.user)
        user_schema['session-id'] = session_id
        return user_schema

    flask.abort(make_response(jsonify(errors=errors.ERR_BAD_SESSION_ID), 400))


@api_calls.route('/register', methods=['POST'])
def register_api():
    """ Registration for users"""

    validated_data = schema.UserRequestSchema().load(flask.request.json or {})

    # Check if role is correct
    if validated_data.get('role') != 'teacher' and validated_data.get('role') != 'student':
        flask.abort(make_response(jsonify(errors=errors.ERR_BAD_ROLE_REQUEST), 400))

    # Create user
    user = models.User.create(**validated_data)

    return schema.UserSchema(many=False).dump(user)


@api_calls.route('/teacher/<int:user_id>/add_course', methods=['PATCH'])
def teacher_course_api(user_id):
    """ Add courses for teacher"""

    validated_data = schema.TeacherCourseRequestSchema().load(flask.request.json or {})

    # Get user
    user = models.User.get_by_id(user_id=user_id)

    if not user:
        flask.abort(make_response(jsonify(errors=errors.ERR_BAD_USER_ID), 400))

    # Check role
    if user.role.name != 'teacher':
        flask.abort(make_response(jsonify(errors=errors.ERR_BAD_USER_ROLE), 400))

    # Get course IDs
    new_ids = validated_data.get('course_id', [])

    if new_ids:
        # Get existing course IDs
        existing_ids = [x.id for x in user.course]

        # Remove course from user
        for course_id in ([int(x) for x in existing_ids if x not in new_ids]):
            course = models.Course.get_by_id(course_id=course_id)

            if not course:
                flask.abort(make_response(jsonify(errors=errors.ERR_BAD_COURSE_ID), 400))

            if course in user.course:
                user.course.remove(course)

        # Add new courses for user
        for course_id in ([int(x) for x in new_ids if x not in existing_ids]):

            course = models.Course.get_by_id(course_id=course_id)

            if not course:
                flask.abort(make_response(jsonify(errors=errors.ERR_BAD_COURSE_ID), 400))

            if course not in user.course:
                user.course.append(course)

        # Edit user for given data
        user.edit(**validated_data)

    return schema.TeacherCourseSchema(many=False).dump(user)


@api_calls.route('/student/<int:student_id>/add_course/<int:course_id>', methods=['POST'])
@api_calls.route('/student/<int:student_id>/course/<int:course_id>', methods=['PATCH'])
@api_calls.route('/student/<int:student_id>/course/<int:course_id>/teacher/<int:teacher_id>', methods=['PATCH'])
def student_course_api(course_id, teacher_id=None, student_id=None):
    """ Add courses for student"""

    if request.method == 'POST':
        validated_data = schema.StudentCourseRequestSchema().load(flask.request.json or {})

        # Check course
        course = models.Course.get_by_id(course_id=course_id)

        if not course:
            flask.abort(make_response(jsonify(errors=errors.ERR_BAD_COURSE_ID), 400))

        # Check student
        user = models.User.get_by_id(user_id=student_id)

        if not user:
            flask.abort(make_response(jsonify(errors=errors.ERR_BAD_USER_ID), 400))

        # Check role
        if user.role.name != 'student':
            flask.abort(make_response(jsonify(errors=errors.ERR_BAD_USER_ROLE), 400))

        # Check if student was previously enrolled in the course
        existing_course = models.StudentCourse.get_course_for_user(student_id=student_id, course_id=course_id)

        if existing_course:
            flask.abort(make_response(jsonify(errors=errors.ERR_STUDENT_ALREADY_ENROLLED_IN_COURSE), 400))

        # Get all existing courses for user
        user_courses = models.StudentCourse.get_all_for_user(student_id=student_id)
        # If user has more than 2 incomplete courses, raise error
        if len(user_courses) >= 2:
            flask.abort(make_response(jsonify(errors=errors.ERR_TOO_MANY_COURSES_FOR_STUDENT), 400))

        # Create student course
        user_course = models.StudentCourse.create(**{
            'course_id': course_id,
            'student_id': student_id,
            'comment': validated_data.get('comment'),

        })

        return schema.UserCourseSchema(many=False).dump(user_course)

    if request.method == 'PATCH':

        # Check student and teacher
        student = models.User.get_by_role(user_id=student_id, role='student')
        teacher = models.User.get_by_role(user_id=teacher_id, role='teacher')

        if not student and not teacher:
            flask.abort(make_response(jsonify(errors=errors.ERR_BAD_USER_ID), 400))

        # Student can add mark and comment
        if not teacher:

            validated_data = schema.EditStudentCourseRequestSchema().load(flask.request.json or {})

            user_course = models.StudentCourse.get_course_for_user(student_id=student_id, course_id=course_id)

            if not user_course:
                flask.abort(make_response(jsonify(errors=errors.ERR_BAD_COURSE_FOR_STUDENT), 400))

            user_course.edit(**validated_data)

        # Teacher can set course to complete
        else:
            validated_data = schema.EditStudentCourseRequestTeacherSchema().load(flask.request.json or {})

            user_course = models.StudentCourse.get_course_for_teacher(teacher_id=teacher_id,
                                                                      student_id=student_id,
                                                                      course_id=course_id)

            if not user_course:
                flask.abort(make_response(jsonify(errors=errors.ERR_BAD_COURSE_FOR_TEACHER), 400))

            if user_course.complete:
                flask.abort(make_response(jsonify(errors=errors.ERR_STUDENT_ALREADY_COMPLETED_THE_COURSE), 400))

            user_course.edit(**validated_data)

        return schema.UserCourseSchema(many=False).dump(user_course)