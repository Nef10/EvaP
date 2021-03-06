from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.forms.models import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render_to_response
from django.template import RequestContext
from django.utils.translation import ugettext as _

from evap.evaluation.models import Contribution, Course, Semester, UserProfile
from evap.evaluation.auth import editor_required, editor_or_delegate_required
from evap.evaluation.tools import questionnaires_and_contributions, STATES_ORDERED, create_voting_form_groups, create_contributor_questionnaires
from evap.contributor.forms import CourseForm, UserForm
from evap.fsr.forms import ContributionForm, ContributorFormSet
from evap.student.forms import QuestionsForm

from collections import OrderedDict

@editor_or_delegate_required
def index(request):
    user = request.user

    sorter = lambda course: STATES_ORDERED.keys().index(course.state)

    own_courses = list(set(Course.objects.filter(contributions__can_edit=True, contributions__contributor=user, state__in=['prepared', 'lecturerApproved', 'approved', 'inEvaluation', 'evaluated', 'reviewed', 'published'])))

    represented_userprofiles = user.represented_users.all()
    represented_users = [profile.user for profile in represented_userprofiles]

    delegated_courses = list(set(Course.objects.exclude(id__in=Course.objects.filter(contributions__can_edit=True, contributions__contributor=user)).filter(contributions__can_edit=True, contributions__contributor__in=represented_users, state__in=['prepared', 'lecturerApproved', 'approved', 'inEvaluation', 'evaluated', 'reviewed', 'published'])))

    all_courses = own_courses + delegated_courses
    all_courses.sort(key=sorter)

    semesters = Semester.objects.all()
    semester_list = [dict(semester_name=semester.name, id=semester.id, courses=[course for course in all_courses if course.semester_id == semester.id]) for semester in semesters]

    return render_to_response("contributor_index.html", dict(semester_list=semester_list, delegated_courses=delegated_courses), context_instance=RequestContext(request))


@editor_required
def profile_edit(request):
    user = request.user
    form = UserForm(request.POST or None, request.FILES or None, instance=UserProfile.objects.get_or_create(user=user)[0])

    if form.is_valid():
        form.save()

        messages.success(request, _("Successfully updated your profile."))
        return redirect('evap.contributor.views.index')
    else:
        return render_to_response("contributor_profile.html", dict(form=form), context_instance=RequestContext(request))


@editor_or_delegate_required
def course_view(request, course_id):
    user = request.user
    course = get_object_or_404(Course, id=course_id)

    # check rights
    if not (course.is_user_editor_or_delegate(user) and course.state in ['prepared', 'lecturerApproved', 'approved', 'inEvaluation', 'evaluated', 'reviewed']):
        raise PermissionDenied

    ContributionFormset = inlineformset_factory(Course, Contribution, formset=ContributorFormSet, form=ContributionForm, extra=0, exclude=('course',))

    form = CourseForm(request.POST or None, instance=course)
    formset = ContributionFormset(request.POST or None, instance=course, queryset=course.contributions.exclude(contributor=None))

    # make everything read-only
    for cform in formset.forms + [form]:
        for name, field in cform.fields.iteritems():
            field.widget.attrs['readonly'] = True
            field.widget.attrs['disabled'] = True

    return render_to_response("contributor_course_form.html", dict(form=form, formset=formset, course=course, edit=False, responsible=course.responsible_contributors_username), context_instance=RequestContext(request))


@editor_or_delegate_required
def course_edit(request, course_id):
    user = request.user
    course = get_object_or_404(Course, id=course_id)

    # check rights
    if not (course.is_user_editor_or_delegate(user) and course.state == 'prepared'):
        raise PermissionDenied

    ContributionFormset = inlineformset_factory(Course, Contribution, formset=ContributorFormSet, form=ContributionForm, extra=0, exclude=('course',))

    form = CourseForm(request.POST or None, instance=course)
    formset = ContributionFormset(request.POST or None, instance=course, queryset=course.contributions.exclude(contributor=None))

    operation = request.POST.get('operation')

    if form.is_valid() and formset.is_valid():
        if operation not in ('save', 'approve'):
            raise PermissionDenied

        form.save(user=user)
        formset.save()

        if operation == 'approve':
            # approve course
            course.contributor_approve()
            course.save()
            messages.success(request, _("Successfully updated and approved course."))
        else:
            messages.success(request, _("Successfully updated course."))

        return redirect('evap.contributor.views.index')
    else:
        return render_to_response("contributor_course_form.html", dict(form=form, formset=formset, course=course, edit=True, responsible=course.responsible_contributors_username), context_instance=RequestContext(request))


@editor_or_delegate_required
def course_preview(request, course_id):
    user = request.user
    course = get_object_or_404(Course, id=course_id)

    # check rights
    if not (course.is_user_editor_or_delegate(user) and course.state in ['prepared', 'lecturerApproved', 'approved', 'inEvaluation', 'evaluated', 'reviewed']):
        raise PermissionDenied

    form_groups = create_voting_form_groups(request, course.contributions.all(), include_self=True)
    course_forms = form_groups[course.general_contribution].values()    
    contributor_questionnaires, errors = create_contributor_questionnaires(form_groups.items())

    return render_to_response(
        "student_vote.html",
        dict(course_forms=course_forms,
            contributor_questionnaires=contributor_questionnaires,
            course=course,
            preview=True),
        context_instance=RequestContext(request))
