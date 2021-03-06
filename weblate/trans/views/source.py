# -*- coding: utf-8 -*-
#
# Copyright © 2012 - 2016 Michal Čihař <michal@cihar.com>
#
# This file is part of Weblate <https://weblate.org/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from django.http import Http404
from django.http.response import HttpResponseServerError
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, get_object_or_404
from django.utils.translation import ugettext as _
from django.utils.encoding import force_text
from django.views.decorators.http import require_POST

from six.moves.urllib.parse import urlencode

from weblate.lang.models import Language
from weblate.trans import messages
from weblate.trans.views.helper import get_subproject
from weblate.trans.models import Translation, Source, Unit
from weblate.trans.forms import (
    PriorityForm, CheckFlagsForm, ScreenshotUploadForm,
    MatrixLanguageForm,
)
from weblate.trans.permissions import (
    can_edit_flags, can_edit_priority, can_upload_screenshot,
)
from weblate.trans.util import render


def get_source(request, project, subproject):
    """
    Returns first translation in subproject
    (this assumes all have same source strings).
    """
    obj = get_subproject(request, project, subproject)
    try:
        return obj, obj.translation_set.all()[0]
    except (Translation.DoesNotExist, IndexError):
        raise Http404('No translation exists in this component.')


def review_source(request, project, subproject):
    """
    Listing of source strings to review.
    """
    obj, source = get_source(request, project, subproject)

    # Grab search type and page number
    rqtype = request.GET.get('type', 'all')
    limit = request.GET.get('limit', 50)
    page = request.GET.get('page', 1)
    checksum = request.GET.get('checksum', '')
    ignored = 'ignored' in request.GET
    expand = False
    query_string = {'type': rqtype}
    if ignored:
        query_string['ignored'] = 'true'

    # Filter units:
    if checksum:
        sources = source.unit_set.filter(checksum=checksum)
        expand = True
    else:
        sources = source.unit_set.filter_type(rqtype, source, ignored)

    paginator = Paginator(sources, limit)

    try:
        sources = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        sources = paginator.page(1)
    except EmptyPage:
        # If page is out of range (e.g. 9999), deliver last page of results.
        sources = paginator.page(paginator.num_pages)

    return render(
        request,
        'source-review.html',
        {
            'object': obj,
            'project': obj.project,
            'source': source,
            'page_obj': sources,
            'query_string': urlencode(query_string),
            'ignored': ignored,
            'expand': expand,
            'title': _('Review source strings in %s') % force_text(obj),
        }
    )


def show_source(request, project, subproject):
    """
    Show source strings summary and checks.
    """
    obj, source = get_source(request, project, subproject)

    return render(
        request,
        'source.html',
        {
            'object': obj,
            'project': obj.project,
            'source': source,
            'title': _('Source strings in %s') % force_text(obj),
        }
    )


@require_POST
@login_required
def edit_priority(request, pk):
    """
    Change source string priority.
    """
    source = get_object_or_404(Source, pk=pk)

    if not can_edit_priority(request.user, source.subproject.project):
        raise PermissionDenied()

    form = PriorityForm(request.POST)
    if form.is_valid():
        source.priority = form.cleaned_data['priority']
        source.save()
    else:
        messages.error(request, _('Failed to change a priority!'))
    return redirect(request.POST.get('next', source.get_absolute_url()))


@require_POST
@login_required
def edit_check_flags(request, pk):
    """
    Change source string check flags.
    """
    source = get_object_or_404(Source, pk=pk)

    if not can_edit_flags(request.user, source.subproject.project):
        raise PermissionDenied()

    form = CheckFlagsForm(request.POST)
    if form.is_valid():
        source.check_flags = form.cleaned_data['flags']
        source.save()
    else:
        messages.error(request, _('Failed to change check flags!'))
    return redirect(request.POST.get('next', source.get_absolute_url()))


@require_POST
@login_required
def upload_screenshot(request, pk):
    """
    Upload screenshot handler.
    """
    source = get_object_or_404(Source, pk=pk)

    if not can_upload_screenshot(request.user, source.subproject.project):
        raise PermissionDenied()

    form = ScreenshotUploadForm(request.POST, request.FILES, instance=source)
    if form.is_valid():
        form.save()
    else:
        for error in form.errors:
            for message in form.errors[error]:
                messages.error(request, message)
    return redirect(request.POST.get('next', source.get_absolute_url()))


@login_required
def matrix(request, project, subproject):
    """Matrix view of all strings"""
    obj = get_subproject(request, project, subproject)

    show = False
    languages = None
    language_codes = None

    if 'lang' in request.GET:
        form = MatrixLanguageForm(obj, request.GET)
        show = form.is_valid()
    else:
        form = MatrixLanguageForm(obj)

    if show:
        languages = Language.objects.filter(
            code__in=form.cleaned_data['lang']
        )
        language_codes = ','.join(languages.values_list('code', flat=True))

    return render(
        request,
        'matrix.html',
        {
            'object': obj,
            'project': obj.project,
            'languages': languages,
            'language_codes': language_codes,
            'languages_form': form,
        }
    )


@login_required
def matrix_load(request, project, subproject):
    """Backend for matrix view of all strings"""
    obj = get_subproject(request, project, subproject)

    try:
        offset = int(request.GET.get('offset', None))
    except ValueError:
        return HttpResponseServerError('Missing offset')
    language_codes = request.GET.get('lang', None)
    if not language_codes or offset is None:
        return HttpResponseServerError('Missing lang')

    # Can not use filter to keep ordering
    translations = [
        get_object_or_404(obj.translation_set, language__code=lang)
        for lang in language_codes.split(',')
    ]

    data = []

    for unit in translations[0].unit_set.all()[offset:offset + 20]:
        units = []
        for translation in translations:
            try:
                units.append(translation.unit_set.get(checksum=unit.checksum))
            except Unit.DoesNotExist:
                units.append(None)

        data.append((unit, units))

    return render(
        request,
        'matrix-table.html',
        {
            'object': obj,
            'data': data,
            'last': translations[0].unit_set.count() <= offset + 20
        }
    )
