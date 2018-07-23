# -*- coding: utf-8 -*-

import re
from zipfile import ZipFile, BadZipfile

from django import forms
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from haystack.forms import SearchForm as HaystackSearchForm
from haystack_static_pages.models import StaticPage

from . import models
from ..examples.models import Example
from ..forum.models import Topic
from ..news.models import News


class FeedbackForm(forms.Form):
    email = forms.EmailField(label=_(u'Email'), required=False)
    message = forms.CharField(label=_(u'Message'), widget=forms.Textarea())
    referer = forms.CharField(required=False, widget=forms.HiddenInput())

    def send(self, request):
        email = self.cleaned_data['email']
        message = self.cleaned_data['message']
        user_agent_data = 'User agent: %s' % request.META.get('HTTP_USER_AGENT')
        timestamp = 'Time: %s' % timezone.now().strftime('%H:%M:%S %m-%d-%Y')
        referer = 'Referer: %s' % self.cleaned_data['referer']
        message = '%s\n\n%s\n%s\n%s' % (message, user_agent_data, timestamp, referer)
        headers = {'Reply-To': email} if email else None

        EmailMessage(settings.FEEDBACK_SUBJECT, message, email, \
                    [a[1] for a in settings.ADMINS], headers=headers).send()


class BookAdminForm(forms.ModelForm):
    archive = forms.FileField(label=_(u'archive'), required=False)

    class Meta:
        model = models.Book
        fields = ('name', 'description', 'archive', 'toc')

    def clean_archive(self):
        archive = self.cleaned_data['archive']

        if archive:
            try:
                error = ZipFile(archive).testzip()
            except BadZipfile:
                error = True
            finally:
                if error:
                    raise forms.ValidationError(_(u'This should be zip archive'))

        return archive

    def update_from_archive(self, archive, obj):
        old_pks = list(models.Page.objects.filter(book=obj).values_list('id', flat=True))
        archive = ZipFile(archive)

        toc = archive.read('toc.py')
        toc = toc.replace('(', '[').replace(')', ']').replace("'", '"')
        obj.toc = toc
        obj.save()

        pics = [name for name in archive.namelist() if name.startswith('pics/') and not name == 'pics/']
        archive.extractall(settings.MEDIA_ROOT, pics)

        appendix_pattern = re.compile(r'^ap(?P<section>[a-z])\.html$')
        ch_pattern = re.compile(r'^ch(?P<ch>\d+)\.html$')
        chs_pattern = re.compile(r'^ch(?P<ch>\d+)s(?P<s>\d+)\.html$')

        for filename in archive.namelist():
            if not filename.split('.')[-1] == 'html':
                continue

            slug = filename[:-5]

            try:
                page = models.Page.objects.get(slug=slug, book=obj)
                old_pks.remove(page.pk)
            except models.Page.DoesNotExist:
                page = models.Page(slug=slug, book=obj)
                #create name if page is new
                if filename == 'index.html':
                    name = u'Первая страница'
                elif chs_pattern.match(filename):
                    r = chs_pattern.match(filename)
                    name = u'Глава %s, раздел %s' % (int(r.group('ch')), int(r.group('s')))
                    page.chapter = r.group('ch')
                    page.section = r.group('s')
                elif ch_pattern.match(filename):
                    r = ch_pattern.match(filename)
                    name = u'Глава %s' % int(r.group('ch'))
                    page.chapter = r.group('ch')
                elif appendix_pattern.match(filename):
                    r = appendix_pattern.match(filename)
                    name = u'Приложение %s' % r.group('section').upper()
                    page.chapter = u'ap'
                    page.section = r.group('section')
                else:
                    name = filename
                page.name = name

            page.content = archive.read(filename)
            page.save()
        models.Page.objects.filter(pk__in=old_pks).delete()
        archive.close()

    def save(self, commit=True):
        obj = super(BookAdminForm, self).save(commit)
        archive = self.cleaned_data['archive']

        if archive:
            self.update_from_archive(archive, obj)

        return obj


def content_choices():
    choices = (
        ('', _(u'All')),
    )
    try:
        choices += (
            (ContentType.objects.get_for_model(Example).pk, _(u'Recipes')),
            (ContentType.objects.get_for_model(Topic).pk, _(u'Forum')),
            (ContentType.objects.get_for_model(News).pk, _(u'News')),
            (ContentType.objects.get_for_model(StaticPage).pk, _(u'Documentation')),
        )
    except RuntimeError:
        pass
    return choices


class SearchForm(HaystackSearchForm):
    content = forms.ChoiceField(choices=content_choices(), label=_(u'Search by'), required=False)

    def search(self):
        if not self.is_valid():
            return self.no_query_found()

        if not self.cleaned_data['q']:
            return self.no_query_found()

        content_type_id = self.cleaned_data.get('content', '')
        sqs = self.searchqueryset.auto_query(self.cleaned_data['q'])

        if content_type_id:
            sqs = sqs.models(ContentType.objects.get(pk=content_type_id).model_class())

        if self.load_all:
            sqs = sqs.load_all()

        return sqs
