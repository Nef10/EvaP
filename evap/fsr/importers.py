from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.translation import ugettext as _

from evap.evaluation.models import Course, UserProfile
from evap.evaluation.tools import is_external_email

import xlrd
from collections import OrderedDict


class UserData(object):
    """Holds information about a user, retrieved from the Excel file."""

    def __init__(self, username='', first_name='', last_name='', title='', email=''):
        self.username = username.strip().lower()
        self.first_name = first_name.strip()
        self.last_name = last_name.strip()
        self.title = title.strip()
        self.email = email.strip().lower()
        self.is_external = False
        if is_external_email(self.email):
            self.is_external = True
            if self.username == '':
                self.username = (self.first_name + '.' + self.last_name + '.ext').lower()

    def store_in_database(self):
        user = User(username=self.username,
                    first_name=self.first_name,
                    last_name=self.last_name,
                    email=self.email)
        user.save()
        profile = UserProfile.get_for_user(user=user)
        profile.title = self.title
        profile.is_external = self.is_external
        profile.save()
        return user

    def update(self, user):
        profile = UserProfile.get_for_user(user=user)

        if not user.first_name:
            user.first_name = self.first_name
        if not user.last_name:
            user.last_name = self.last_name
        if not user.email:
            user.email = self.email
        if not profile.title:
            profile.title = self.title
        if profile.needs_login_key:
            profile.refresh_login_key()

        user.save()
        profile.save()


class CourseData(object):
    """Holds information about a course, retrieved from the Excel file."""

    def __init__(self, name_de='', name_en='', kind='', degree=''):
        self.name_de = name_de.strip()
        self.name_en = name_en.strip()
        self.kind = kind.strip()
        self.degree = degree.strip()

    def store_in_database(self, vote_start_date, vote_end_date, semester):
        course = Course(name_de=self.name_de,
                        name_en=self.name_en,
                        kind=self.kind,
                        vote_start_date=vote_start_date,
                        vote_end_date=vote_end_date,
                        semester=semester,
                        degree=self.degree)
        course.save()
        return course


class ExcelImporter(object):
    def __init__(self, request):
        self.associations = OrderedDict()
        self.request = request

    def for_each_row_in_excel_file_do(self, excel_file, execute_per_row):
        book = xlrd.open_workbook(file_contents=excel_file.read())

        # read the file row by row, sheet by sheet
        for sheet in book.sheets():
            try:
                for row in range(1, sheet.nrows):
                    execute_per_row(sheet.row_values(row), sheet.name, row)

                messages.success(self.request, _(u"Successfully read sheet '%s'.") % sheet.name)
            except Exception:
                messages.warning(self.request, _(u"A problem occured while reading sheet '%s'.") % sheet.name)
                raise
        messages.success(self.request, _(u"Successfully read excel file."))

    def read_one_enrollment(self, data, sheet_name, row_id):
        if len(data) != 13:
            messages.warning(self.request, _(u"Invalid line %(row)s in sheet '%(sheet)s', beginning with '%(beginning)s', number of columns: %(ncols)s") % dict(sheet=sheet_name, row=row_id, ncols=len(data), beginning=data[0] if len(data) > 0 else ''))
        else:            
            student_data = UserData(username=data[3], first_name=data[2], last_name=data[1], email=data[4])
            contributor_data = UserData(username=data[11], first_name=data[10], last_name=data[9], title=data[8], email=data[12])
            course_data = CourseData(name_de=data[6], name_en=data[7], kind=data[5], degree=data[0][:-7])
            # store data objects together with the data source location for problem tracking
            self.associations[(sheet_name, row_id)] = (student_data, contributor_data, course_data)

    def read_one_user(self, data, sheet_name, row_id):
        if len(data) != 5:
            messages.warning(self.request, _(u"Invalid line %(row)s in sheet '%(sheet)s', beginning with '%(beginning)s', number of columns: %(ncols)s") % dict(sheet=sheet_name, row=row_id, ncols=len(data), beginning=data[0] if len(data) > 0 else ''))
        else:
            user_data = UserData(username=data[0], title=data[1], first_name=data[2], last_name=data[3], email=data[4])
            # store data objects together with the data source location for problem tracking
            self.associations[(sheet_name, row_id)] = (user_data)

    def validate_and_fix_enrollments(self):
        """Validates the internal integrity of the data read by read_file and
        fixes and inferres data if possible. Should not validate against data
        already in the database."""

        for (sheet, row), (student_data, contributor_data, course_data) in self.associations.items():
            if student_data.email is None or student_data.email == "":
                student_data.email = student_data.username + "@student.hpi.uni-potsdam.de"

    def save_enrollments_to_db(self, semester, vote_start_date, vote_end_date):
        """Stores the read and validated data in the database. Errors might still
        occur because the previous validation does check not for consistency with
        the data already in the database."""

        with transaction.atomic():
            course_count = 0
            student_count = 0
            contributor_count = 0
            for (sheet, row), (student_data, contributor_data, course_data) in self.associations.items():
                try:
                    # create or retrieve database objects
                    try:
                        student = User.objects.get(username__iexact=student_data.username)
                        student_data.update(student)
                    except User.DoesNotExist:
                        student = student_data.store_in_database()
                        student_count += 1

                    try:
                        contributor = User.objects.get(username__iexact=contributor_data.username)
                        contributor_data.update(contributor)
                    except User.DoesNotExist:
                        contributor = contributor_data.store_in_database()
                        contributor_count += 1

                    try:
                        course = Course.objects.get(semester=semester, name_de=course_data.name_de, degree=course_data.degree)
                    except Course.DoesNotExist:
                        course = course_data.store_in_database(vote_start_date, vote_end_date, semester)
                        course.contributions.create(contributor=contributor, course=course, responsible=True, can_edit=True)
                        course_count += 1

                    # connect database objects
                    course.participants.add(student)

                except Exception as e:
                    messages.error(self.request, _("A problem occured while writing the entries to the database. The original data location was row %(row)d of sheet '%(sheet)s'. The error message has been: '%(error)s'") % dict(row=row, sheet=sheet, error=e))
                    raise
            messages.success(self.request, _("Successfully created %(courses)d course(s), %(students)d student(s) and %(contributors)d contributor(s).") % dict(courses=course_count, students=student_count, contributors=contributor_count))

    def save_users_to_db(self):
        """Stores the read data in the database. Errors might still
        occur because of the data already in the database."""

        with transaction.atomic():
            users_count = 0
            for (sheet, row), (user_data) in self.associations.items():
                try:
                    # create or retrieve database objects
                    try:
                        user = User.objects.get(username__iexact=user_data.username)
                        user_data.update(user)
                    except User.DoesNotExist:
                        user = user_data.store_in_database()
                        users_count += 1

                except Exception as e:
                    messages.error(self.request, _("A problem occured while writing the entries to the database. The original data location was row %(row)d of sheet '%(sheet)s'. The error message has been: '%(error)s'") % dict(row=row, sheet=sheet, error=e))
                    raise
            messages.success(self.request, _("Successfully created %(users)d user(s).") % dict(users=users_count))

    @classmethod
    def process_enrollments(cls, request, excel_file, semester, vote_start_date, vote_end_date):
        """Entry point for the view."""
        try:
            importer = cls(request)
            importer.for_each_row_in_excel_file_do(excel_file, importer.read_one_enrollment)
            importer.validate_and_fix_enrollments()
            importer.save_enrollments_to_db(semester, vote_start_date, vote_end_date)
        except Exception as e:
            messages.error(request, _(u"Import finally aborted after exception: '%s'" % e))
            if settings.DEBUG:
                # re-raise error for further introspection if in debug mode
                raise

    @classmethod
    def process_users(cls, request, excel_file):
        """Entry point for the view."""
        try:
            importer = cls(request)
            importer.for_each_row_in_excel_file_do(excel_file, importer.read_one_user)
            importer.save_users_to_db()
        except Exception as e:
            messages.error(request, _(u"Import finally aborted after exception: '%s'" % e))
            if settings.DEBUG:
                # re-raise error for further introspection if in debug mode
                raise