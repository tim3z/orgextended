import re
import os
import time
import sublime, sublime_plugin
import datetime
import fnmatch
import OrgExtended.orgparse.node as node
import OrgExtended.orgparse.date as orgdate
import OrgExtended.orgutil.util as util
import OrgExtended.orgutil.navigation as nav
import OrgExtended.orgutil.template as templateEngine
import OrgExtended.orgduration as dur
import logging
import sys
import traceback
import OrgExtended.orgfolding as folding
import OrgExtended.orgdb as db
import OrgExtended.orgdatepicker as dpick
import OrgExtended.asettings as sets
import OrgExtended.pymitter as evt
import OrgExtended.orginsertselected as insSel
import calendar

import dateutil.rrule as dr
import dateutil.parser as dp
import dateutil.relativedelta as drel

log = logging.getLogger(__name__)
AGENDA_VIEW = "Org Mode Agenda"
TODO_VIEW   = "Org Todos"

ViewMappings = {}


def ReloadAllUnsavedBuffers():
    sheets = sublime.active_window().sheets()
    for sheet in sheets:
        view = sheet.view()
        if(view and util.isPotentialOrgFileOrBuffer(view)):
            db.Get().FindInfo(view)

def IsRawDate(ts):
    return isinstance(ts,datetime.date) or isinstance(ts,datetime.datetime)

def EnsureDateTime(ts):
    if(ts and not isinstance(ts,datetime.datetime)):
        return datetime.datetime.combine(ts, datetime.datetime.min.time())
    return ts

def EnsureDate(ts):
    if(isinstance(ts,datetime.datetime)):
        return ts.date()
    return ts

def FindMappedView(view):
    if(view.name() in ViewMappings):
        return ViewMappings[view.name()]
    log.debug("Could not find view named: " + view.name())
    return None

def move_file_other_group(myview, view):
    window = sublime.active_window()
    if (window.num_groups() < 2):
        #self.window.run_command('clone_file')
        window.set_layout({
            "cols": [0.0, 0.5, 1.0],
            "rows": [0.0, 1.0],
            "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
            })
        mygroup    = 0
        othergroup = 1
    else:
        window.focus_view(view)
        mygroup    = 1
        othergroup = 0
        if (window.get_view_index(myview)[0] == 0):
            othergroup = 1
            mygroup    = 0
    window.focus_view(view)
    window.run_command('move_to_group', {'group': othergroup})
    window.run_command('focus_group', {'group': mygroup})
    window.focus_view(myview)
        #view0 = self.window.active_view_in_group(0)
        #view1 = self.window.active_view_in_group(1)

        # Same file open in each of the two windows, cull to 1 if possible
        #if (view0.file_name() == view1.file_name()):
        #    self.window.focus_view(view1)

#class CloneFileToNewViewCommand(sublime_plugin.WindowCommand):
#    def run(self):

def get_view_for_silent_edit_file(file):
    # First check all sheets for this file.
    window = sublime.active_window()
    view = window.find_open_file(file.filename)
    if(view):
        return view
    # Okay the file is not opened, we have to open it
    # but we don't want it having focus
    # So keep the old view so we can refocus just to
    # be sure.
    currentView = window.active_view()
    view = window.open_file(file.filename, sublime.ENCODED_POSITION)
    window.focus_view(currentView)
    return view

def CreateUniqueViewNamed(name, mapped=None):
    # Close the view if it exists
    win = sublime.active_window()
    for view in win.views():
        if view.name() == name:
            win.focus_view(view)
            win.run_command('close')
    win.run_command('new_file')
    view = win.active_view()
    view.set_name(name)
    # TODO: Change this.
    view.set_syntax_file("Packages/OrgExtended/orgagenda.sublime-syntax")
    ViewMappings[view.name()] = mapped
    return view


def IsPhone(n):
    return n and n.todo and "PHONE" in n.todo

def IsMeeting(n):
    return n and n.todo and "MEETING" in n.todo

def IsNote(n):
    return n and n.todo and "NOTE" in n.todo

def IsTodo(n):
    return n.todo and n.todo in n.env.todo_keys

def IsDone(n):
    return n.todo and n.todo in n.env.done_keys

def IsArchived(n):
    return "ARCHIVE" in n.tags

def HasChildTasks(n):
    for c in n.children:
        if(IsTodo(c)):
            return True
    return False

def HasTodoAncestor(n):
    if(not n or n.is_root()):
        return False
    return (n.parent and not n.parent.is_root() and (IsTodo(n.parent) or HasTodoAncestor(n.parent)))

# Task that belongs to a project. This means we have to have a parent and that parent has to be
# identified as a project.
def IsProjectTask(n):
    if(not n or n.is_root()):
        return False
    return (IsTodo(n) and n.parent and not n.parent.is_root() and IsProject(n.parent))

def IsBlockedProject(n):
    if(IsProject(n) and n.num_children > 0):
        isBlocked = True
        for c in n.children:
            if(c.todo and c.todo == 'NEXT'):
                isBlocked = False
        return isBlocked
    else:
        return False

def IsProjectTodoWithTodos(n):
    if(IsTodo(n) and n.num_children > 0):
        for c in n.children:
            if(IsTodo(c)):
                return True
    return False

def IsProjectTaskWithProjectTag(n):
    if(n):
        tags = n.shallow_tags
        if("PROJECT" in tags or "Project" in tags or "project" in tags):
            #print("PROJ: " + n.heading)
            return True
    return False

def IsProjectTaskWithProjectProperty(n):
    if(n and (n.get_property("PROJECT") or n.get_property("Project") or n.get_property("project"))):
        return True
    return False

def IsProject(n):
    projType = sets.Get("agendaProjectIs", "nested_todo").lower()
    if(projType == "nested_todo"):
        return IsProjectTodoWithTodos(n)
    if(projType == "tag"):
        return IsProjectTaskWithProjectTag(n)
    if(projType == "property"):
        return IsProjectTaskWithProjectProperty(n)
    return IsProjectTodoWithTodos(n)

def DatesEqual(a, b):
    if not type(a) == datetime.date:
        a = a.date()
    if not type(b) == datetime.date:
        b = b.date()
    return a == b

def IsInMonthCheck(t, now):
    if(IsRawDate(t)):
        dateT = t
    else:
        dateT = datetime.date(t.start.year, t.start.month, t.start.day)

    if (dateT.year == now.year):
        if (dateT.month >= (now.month-1) and dateT.month <= (now.month+1)):
            return True
        else:
            return False
    if (dateT.year == now.year + 1) and (dateT.month == 0 and now.month == 11):
        return True
    if (dateT.year == now.year - 1) and (dateT.month == 11 and now.month == 0):
        return True
    return False

def IsInMonth(n, now):
    if(not n):
        return (None,None)
    if(n):
        timestamps = n.get_timestamps(active=True,point=True,range=True)
        for t in timestamps:
            if(t.repeating):
                next = t.next_repeat_from(now)
                if(IsInMonthCheck(next, now)):
                    if(IsRawDate(t)):
                        return (now,True)
                    else:
                        return (now, True)
            else:
                if(IsInMonthCheck(t, now)):
                    return (t,False)
        if(n.scheduled):
            if(IsInMonthCheck(n.scheduled, now)):
                return (n.scheduled.start,n.scheduled.repeating)
    return (None,None)

def IsOnDate(n, date):
    if isinstance(date, datetime.datetime):
        date = date.date()
    today = datetime.date.today()
    # 4 months of per day scheduling is the maximum
    # we are willing to loop to avoid crazy slow loops.
    kMaxLoops = sets.GetInt("agendaMaxScheduledIterations", 120)
    timestamps = n.get_timestamps(active=True,point=True,range=True)
    for t in timestamps:
        if t.repeating:
            if DatesEqual(t.start, date):
                return t
            next = EnsureDateTime(t.start)
            loopcount = 0
            while next <= EnsureDateTime(date) and loopcount <= kMaxLoops:
                if DatesEqual(next, date):
                    return next
                next = t.next_repeat_from(next)
                loopcount += 1
        else:
            if t.has_overlap(date):
                return t
    if n.scheduled:
        if n.scheduled.repeating:
            next = n.scheduled.start
            loopcount = 0
            while EnsureDateTime(next) <= EnsureDateTime(date) and loopcount <= kMaxLoops:
                if DatesEqual(next, date):
                    return next
                next = n.scheduled.next_repeat_from(EnsureDateTime(next))
                loopcount += 1
        else:
            schedule_start = EnsureDateTime(n.scheduled.start)
            if DatesEqual(date, schedule_start) or (schedule_start.date() < today and DatesEqual(date, today)):
                return today
    if n.deadline:
        start = EnsureDateTime(display_deadline_start(n.deadline))
        if start <= date:
            return n.deadline
        if n.deadline.repeating:
            next = n.deadline.start
            loopcount = 0
            while(EnsureDateTime(next) <= EnsureDateTime(date) and loopcount <= kMaxLoops):
                if DatesEqual(next, date):
                    return next
                next = n.deadline.next_repeat_from(EnsureDateTime(next))
                loopcount += 1
    return None

def IsAllDay(n, today):
    if not n:
        return None
    timestamps = n.get_timestamps(active=True, point=True, range=True)
    for t in timestamps:
        if t.repeating:
            dt = t.next_repeat_from(today)
            if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
                return dt
        else:
            if(t.has_end() or t.has_time()):
                continue
            return t
    if n.scheduled:
        if n.scheduled.repeating:
            dt = n.scheduled.next_repeat_from(today)
            if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
                return n.scheduled
        else:
            if (not n.scheduled.has_end() and not n.scheduled.has_time()) or n.scheduled.before(today):
                return n.scheduled
    if n.deadline:
        dt = display_deadline_start(n.deadline)
        if isinstance(dt, datetime.date):
            return True
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
            return today
    return None

def HasTimestamp(n):
    if(not n):
        return False
    timestamps = n.get_timestamps(active=True,point=True,range=True)
    return n.scheduled or (timestamps and len(timestamps) > 0) or n.deadline

def IsInHourBracket(s, e, hour):
    if not e:
        # TODO Make this configurable
        e = s + datetime.timedelta(minutes=30)
    # Either this task is a ranged task OR it is a single point task
    # Ranged tasks have to fit within the hour, point tasks have to
    if Overlaps(s.hour*60 + s.minute, e.hour*60 + e.minute, hour*60, (hour+1)*60):
        return True


def IsInHour(n, hour, today):
    if not n:
        return None
    timestamps = n.get_timestamps(active=True, point=True,range=True)
    if timestamps:
        for t in timestamps:
            if t.has_time():
                if t.repeating:
                    next = t.next_repeat_from(today)
                    if(next.hour == hour):
                        return next
                else:
                    s = t.start
                    e = t.end
                    if IsInHourBracket(s,e,hour):
                        return t
    if n.scheduled and n.scheduled.has_time():
        if(n.scheduled.repeating):
            next = n.scheduled.next_repeat_from(today)
            if next.hour == hour:
                return next
            else:
                return None
        s = EnsureDateTime(n.scheduled.start)
        e = EnsureDateTime(n.scheduled.end)
        if IsInHourBracket(s,e,hour):
            return n.scheduled
    if n.deadline:
        s = EnsureDateTime(n.deadline.start)
        e = EnsureDateTime(n.deadline.end)
        if IsInHourBracket(s,e,hour):
            return n.deadline
    return None


def Overlaps(s,e,rs,re):
    return not (s >= re or e <= rs)

def IsInHourAndMinuteBracket(s,e,hour,mstart,mend):
    if not e:
        # TODO Make this configurable
        e = s + datetime.timedelta(minutes=30)

    # Either this task is a ranged task OR it is a single point task
    # Ranged tasks have to fit within the hour, point tasks have to
    return Overlaps(s.hour*60 + s.minute, e.hour*60 + e.minute, hour*60 + mstart, hour*60 + mend)

def IsInHourAndMinute(n, hour, mstart, mend, today):
    if(not n):
        return None
    timestamps = n.get_timestamps(active=True, point=True,range=True)
    if(timestamps):
        for t in timestamps:
            if(t.has_time()):
                if(t.repeating):
                    next = t.next_repeat_from(today)
                    if(next.hour == hour):
                        return next
                else:
                    s = t.start
                    e = t.end
                    if(IsInHourAndMinuteBracket(s,e,hour,mstart,mend)):
                        return t

    if(n.scheduled and n.scheduled.has_time()):
        if(n.scheduled.repeating):
            next = n.scheduled.next_repeat_from(today)
            if(next.hour == hour):
                return next
        s = n.scheduled.start
        e = n.scheduled.end
        if(IsInHourAndMinuteBracket(s,e,hour,mstart,mend)):
            return n.scheduled

    if(n.deadline):
        s = EnsureDateTime(n.deadline.start)
        e = EnsureDateTime(n.deadline.end)
        if(IsInHourAndMinuteBracket(s,e,hour,mstart,mend)):
            return n.deadline
    return None


def distanceFromStart(e, hour, minSlot):
    ts = e['ts']
    if(IsRawDate(ts)):
        rv = 5*(hour - ts.hour) + (minSlot - int(ts.minute/12))
    else:
        rv = 5*(hour - ts.start.hour) + (minSlot - int(ts.start.minute/12))
    return rv

def display_deadline_start(deadline_date):
    prewarn_duration = datetime.timedelta(days=14)
    if deadline_date.warning:
        prewarn_duration = deadline_date.warn_rule
    return deadline_date.start - prewarn_duration

# ================================================================================
# IDEA Make a base class that has all the functionality needed to
#      render an agenda view. Then create an agenda folder with
#      all my views, like dynamic blocks.
#
#      The goal being to allow people to extend and create custom
#      agenda views from their user folder, without having to
#      write all the "stuff"
#
#      Also create a BUNCH of filters that filter by tag, properties
#      and other things so there are example versions of each.
#
# statefilter
# tagfilter
# priorityfilter
class AgendaBaseView:
    def __init__(self, name, setup=True, **kwargs):
        self.name = kwargs.get("title", name)
        self.SetTagFilter(kwargs)
        self.SetPriorityFilter(kwargs)
        self.SetStateFilter(kwargs)
        self.SetDurationFilter(kwargs)
        self.SetClockedDurationFilter(kwargs)
        self.SetDateFilter(kwargs)
        self.hasclock = "hasclock" in kwargs
        self.clockedtoday = "clockedtoday" in kwargs
        self.clockfilter = "clockfilter" in kwargs
        self.hasclose = "hasclose" in kwargs
        self.hasdeadline = "hasdeadline" in kwargs
        self.hasschedule = "hasschedule" in kwargs
        self.noclock = "noclock" in kwargs
        self.noclose = "noclose" in kwargs
        self.nodeadline = "nodeadline" in kwargs
        self.noschedule = "noschedule" in kwargs
        self.onlyTasks  = "onlytasks" in kwargs
        self.haschildtasks = "haschildtasks" in kwargs
        self.nochildtasks = "nochildtasks" in kwargs
        self.hastodoancestor = "hastodoancestor" in kwargs
        self.notodoancestor = "notodoancestor" in kwargs

        if(setup):
            self.SetupView()
        else:
            self.BasicSetup()

    # Default view does not have a filter view
    # Only todo view
    def OpenFilterView(self):
        pass

    def BasicSetup(self):
        self.InitializeSelectedDate()
        self.entries = []

    def SetDurationFilter(self,kwargs):
        self._beforeDuration     = []
        self._afterDuration      = []
        if("durationfilter" in kwargs):
            self._durationFilter = kwargs["durationfilter"]
        else:
            self._durationFilter = None
            return
        tags = self._durationFilter.split(' ')
        for tag in tags:
            tag = tag.strip()
            if not tag or len(tag) <= 0:
                continue
            m = RE_IN_OUT_TAG.search(tag)
            if(m):
                inout = m.group('inout')
                tagdata = m.group('tag')
                if(not inout or inout == '+'):
                    self._beforeDuration.append(dur.OrgDuration.Parse(tagdata.strip()))
                else:
                    self._afterDuration.append(dur.OrgDuration.Parse(tagdata.strip()))

    def SetClockedDurationFilter(self,kwargs):
        self._beforeClockedDuration     = []
        self._afterClockedDuration      = []
        if("clockfilter" in kwargs):
            self._clockedDurationFilter = kwargs["clockfilter"]
        else:
            self._clockedDurationFilter = None
            return
        tags = self._clockedDurationFilter.split(' ')
        for tag in tags:
            tag = tag.strip()
            if not tag or len(tag) <= 0:
                continue
            m = RE_IN_OUT_TAG.search(tag)
            if(m):
                inout = m.group('inout')
                tagdata = m.group('tag')
                if(not inout or inout == '+'):
                    self._beforeClockedDuration.append(dur.OrgDuration.Parse(tagdata.strip()))
                else:
                    self._afterClockedDuration.append(dur.OrgDuration.Parse(tagdata.strip()))

    def SetStateFilter(self,kwargs):
        if("statefilter" in kwargs):
            self._stateFilter = kwargs["statefilter"]
        else:
            self._stateFilter = None
            return
        self._inStateTags     = []
        self._oneofStateTags  = []
        self._outStateTags    = []
        tags = self._stateFilter.split(' ')
        for tag in tags:
            tag = tag.strip()
            if not tag or len(tag) <= 0:
                continue
            m = RE_IN_OUT_TAG.search(tag)
            if(m):
                inout = m.group('inout')
                tagdata = m.group('tag')
                if(not inout or inout == '+'):
                    self._inStateTags.append(tagdata.strip())
                elif(inout == '|'):
                    self._oneofStateTags.append(tagdata.strip())
                else:
                    self._outStateTags.append(tagdata.strip())

    def SetPriorityFilter(self,kwargs):
        if("priorityfilter" in kwargs):
            self._priorityFilter = kwargs["priorityfilter"]
        else:
            self._priorityFilter = None
            return
        self._inPriorityTags     = []
        self._oneofPriorityTags  = []
        self._outPriorityTagsags    = []
        tags = self._priorityFilter.split(' ')
        for tag in tags:
            tag = tag.strip()
            if not tag or len(tag) <= 0:
                continue
            m = RE_IN_OUT_TAG.search(tag)
            if(m):
                inout = m.group('inout')
                tagdata = m.group('tag')
                if(not inout or inout == '+'):
                    self._inPriorityTags.append(tagdata.strip())
                elif(inout == '|'):
                    self._oneofPriorityTags.append(tagdata.strip())
                else:
                    self._outPriorityTags.append(tagdata.strip())

    def SetTagFilter(self,kwargs):
        if("tagfilter" in kwargs):
            self._tagfilter = kwargs["tagfilter"]
        else:
            self._tagfilter = None
            return
        self._intags     = []
        self._oneoftags  = []
        self._outtags    = []
        tags = self._tagfilter.split(' ')
        for tag in tags:
            tag = tag.strip()
            if not tag or len(tag) <= 0:
                continue
            m = RE_IN_OUT_TAG.search(tag)
            if(m):
                inout = m.group('inout')
                tagdata = m.group('tag')
                if(not inout or inout == '+'):
                    self._intags.append(tagdata.strip())
                elif(inout == '|'):
                    self._oneoftags.append(tagdata.strip())
                else:
                    self._outtags.append(tagdata.strip())

    def SetDateFilter(self, kwargs):
        self._startDoneDateComparator = None
        self._endDoneDateComparator = None

        if "datefilter" not in kwargs:
            return

        DATE_PATTERN = "([><=]+)(\d+)"
        datefilter = kwargs["datefilter"].split(" ")
        for cond in datefilter:
            match = re.search(DATE_PATTERN, cond)
            if match is None:
                continue
            operator = match.group(1)
            date_str = match.group(2)

            try:
                date = dp.parse(date_str)
            except:
                log.error("Failed to parse datefilter")
                continue

            if operator == ">":
                self._startDoneDateComparator = lambda d, date=date: d is not None and date < d
            if operator == ">=":
                self._startDoneDateComparator = lambda d, date=date: d is not None and date <= d
            if operator == "<":
                self._endDoneDateComparator = lambda d, date=date: d is not None and d < date
            if operator == "<=":
                date = date + datetime.timedelta(days=1)
                self._endDoneDateComparator = lambda d, date=date: d is not None and d < date

    def MatchHas(self, node):
        if(self.hasclock and not node.clock):
            return False
        if(self.hasdeadline and not node.deadline):
            return False
        if(self.hasclose and not node.closed):
            return False
        if(self.hasschedule and not node.scheduled):
            return False
        if(self.haschildtasks and not HasChildTasks(node)):
            return False
        if(self.hastodoancestor and not HasTodoAncestor(node)):
            return False
        if(self.noclock and node.clock):
            return False
        if(self.nodeadline and node.deadline):
            return False
        if(self.noclose and node.closed):
            return False
        if(self.noschedule and node.scheduled):
            return False
        if(self.nochildtasks and HasChildTasks(node)):
            return False
        if(self.notodoancestor and HasTodoAncestor(node)):
            return False
        return True

    def MatchTags(self, node):
        if(not self._tagfilter):
            return True
        if(self._intags and len(self._intags) > 0 and not all(elem in node.tags  for elem in self._intags)):
            return False
        if(self._outtags and any(elem in node.tags for elem in self._outtags)):
            return False
        if(self._oneoftags and len(self._oneoftags) > 0 and not any(elem in node.tags for elem in self._oneoftags)):
            return False
        return True

    def MatchPriorities(self, node):
        if(not self._priorityFilter):
            return True
        if(self._inPriorityTags and len(self._inPriorityTags) > 0 and not all(elem in node.priority  for elem in self._inPriorityTags)):
            return False
        if(self._outPriorityTags and any(elem in node.priority for elem in self._outPriorityTags)):
            return False
        if(self._oneofPriorityTags and len(self._oneofPriorityTags) > 0 and not any(elem in node.priority for elem in self._oneofPriorityTags)):
            return False
        return True

    def MatchState(self, node):
        t = node.todo
        if(not node.todo):
            t = ""
        if(not self._stateFilter):
            return True
        if(self._inStateTags and len(self._inStateTags) > 0 and not all(re.search(elem,t) for elem in self._inStateTags)):
            return False
        if(self._outStateTags and any(re.search(elem,t) for elem in self._outStateTags)):
            return False
        if(self._oneofStateTags and len(self._oneofStateTags) > 0 and not any(re.search(elem,t) for elem in self._oneofStateTags)):
            return False
        return True

    def MatchDuration(self, node):
        t = node.closed
        if(t and self._afterDuration and any(not t.after_duration(elem) for elem in self._afterDuration)):
            return False
        if self._beforeDuration:
            s = node.scheduled
            d = node.deadline
            ts = node.get_timestamps()
            if(s and any(s and s.before_duration(elem) for elem in self._beforeDuration)):
                return True
            if(d and any(d and d.before_duration(elem) for elem in self._beforeDuration)):
                return True
            if(ts and len(ts) > 0 and any(ts[0] and ts[0].before_duration(elem) for elem in self._beforeDuration)):
                return True
            return False
        return True

    def MatchDate(self, node):
        start = node.closed.start
        end = node.closed.end if node.closed.end is not None else start
        if self._startDoneDateComparator is not None \
                and not self._startDoneDateComparator(start):
            return False
        if self._endDoneDateComparator is not None \
                and not self._endDoneDateComparator(end):
            return False
        return True

    def MatchClock(self, node):
        if self.clockedtoday:
            if not node.clock:
                return False
            now = datetime.datetime.now()
            for c in node.clock:
                if c.start.date() == now.date():
                    return True
                if c.end.date() == now.date():
                    return True
            return False
        if self.clockfilter:
            if not node.clock:
                return False
            for t in node.clock:
                if t:
                    if(self._afterClockedDuration and any(not t.after_duration(elem) for elem in self._afterClockedDuration)):
                        return False
                    if(self._beforeClockedDuration and any(not t.before_duration(elem) for elem in self._beforeClockedDuration)):
                        return False
        return True


    def SetupView(self):
        self.view = CreateUniqueViewNamed(self.name, self)
        self.view.set_read_only(True)
        self.view.set_scratch(True)
        self.view.set_name(self.name)
        self.BasicSetup()
        self.LoadAndFilterEntries()
        # Keep ourselves attached to this agenda
        # This doesn't work BOO
        self.view.agenda = self

    def DoRenderView(self,edit, clear = False):
        self.StartEditing()
        self.RenderView(edit, clear)
        self.DoneEditing()

    def OpenFilterView(self):
        first = True
        for v in self.agendaViews:
            v.view = self.view
            v.OpenFilterView()


    def InsertAgendaHeading(self, edit):
        if(hasattr(self,'_tagfilter') and self._tagfilter):
            self.view.insert(edit, self.view.size(), self.name + "\t\t TAGS( " + self._tagfilter + " )\n")
        else:
            self.view.insert(edit, self.view.size(), self.name + "\n")

    def InitializeSelectedDate(self, selected=None):
        self.selected_date = selected or datetime.datetime.now()

    def UpdateSelectedDate(self, date):
        self.selected_date = date
        self.entries = []

    # You have to bookend your editing session with these
    def StartEditing(self):
        self.view.set_read_only(False)

    def DoneEditing(self):
        self.view.set_read_only(True)

    def RestoreCursor(self, pos):
        # Restore the cursor
        self.view.sel().clear()
        self.view.sel().add(pos)

    def ClearEntriesAt(self):
        for e in self.entries:
            if 'at' in e:
                del e['at']

    def MarkEntryAt(self, entry, ts= None):
        if(not 'at' in entry):
            entry['at'] = []
        entry['at'].append(self.view.rowcol(self.view.size())[0])
        entry['ts'] = ts

    def MarkEntryAtRegion(self, entry, reg, ts=None):
        if(not 'at' in entry):
            entry['at'] = []
        entry['at'].append(reg)
        entry['ts'] = ts

    def At(self, row, col):
        for e in self.entries:
            if 'at' in e:
                for  ea in e['at']:
                    if(type(ea) == int):
                        if(ea == row):
                            return (e['node'], e['file'])
                    elif(type(ea) == sublime.Region):
                        if(ea.contains(self.view.text_point(row,col))):
                            return (e['node'], e['file'])
        return (None, None)

    def Clear(self, edit):
        self.StartEditing()
        self.view.erase(edit, sublime.Region(0,self.view.size()))
        self.DoneEditing()

    # ----------------------------------------------
    # These are user extended views!
    def RenderView(self, edit, clear=False):
        pass

    def generate_entries(self):
        allowOutsideOrgDir = sets.Get("agendaIncludeFilesOutsideOrgDir", False)
        for file in db.Get().Files:
            # Skip over files not in orgDir
            if not file.isOrgDir and not allowOutsideOrgDir:
                continue
            for n in file.org[1:]:
                yield { "node": n, "file": file }

    def set_entries_filtered(self, entries):
        self.entries = [
            e for e in entries if (
                    self.MatchHas(e['node'])
                    and self.MatchPriorities(e['node'])
                    and self.MatchTags(e['node'])
                    and self.MatchState(e['node'])
                    and self.MatchDuration(e['node'])
                    and self.MatchDate(e['node'])
                    and self.MatchClock(e['node'])
                    and self.FilterEntry(e['node'], e['file'])
                )
        ]

    def LoadAndFilterEntries(self):
        self.set_entries_filtered(self.generate_entries())

    def FilterEntry(self, node, file):
        return True

def IsBeforeNow(ts, now):
    if(isinstance(ts,orgdate.OrgDate)):
        return ts and (not ts.has_time() or ts.start.time() < now.time())
    elif(ts and isinstance(ts,datetime.datetime)):
        return ts.time() < now.time()
    else:
        return False

def IsAfterNow(ts, now):
    if(isinstance(ts,orgdate.OrgDate)):
        return ts and ts.has_time() and ts.start.time() >= now.time()
    elif(ts and isinstance(ts,datetime.datetime)):
        return ts.time() >= now.time()
    else:
        return False

# ============================================================
class CalendarView(AgendaBaseView):
    def __init__(self, name, setup=True,**kwargs):
        super(CalendarView, self).__init__(name, setup, **kwargs)
        firstDayIndex = sets.GetWeekdayIndexByName(sets.Get("firstDayOfWeek","Sunday"))
        self.dv = dpick.DateView("orgagenda.now",firstDayIndex = firstDayIndex)

    def UpdateSelectedDate(self, date):
        self.selected_date = date
        self.dv.MoveCDateToDate(self.selected_date)

    def AddRepeating(self, date):
        self.dv.AddToDayHighlights(date, "repeat", "orgagenda.blocked")

    def AddTodo(self, date):
        if(isinstance(date,orgdate.OrgDate)):
            date = date.start
        self.dv.AddToDayHighlights(date, "todo", "orgagenda.todo", sublime.DRAW_NO_FILL)
    #def AddToDayHighlights(self, date, key, hightlight, drawtype = sublime.DRAW_NO_OUTLINE):
    def RenderView(self, edit, clear=False):
        self.InsertAgendaHeading(edit)
        self.dv.SetView(self.view)
        self.dv.Render(self.selected_date)
        toHighlight = []
        for entry in self.entries:
            n = entry['node']
            ts, repeating = IsInMonth(n, self.selected_date)
            if(ts):
                self.AddTodo(ts)
            if(ts and repeating):
                self.AddRepeating(ts)

    def FilterEntry(self, n, filename):
        return (not self.onlyTasks or (IsTodo(n) and not IsDone(n) and not IsArchived(n))) and not IsProject(n) and HasTimestamp(n)

def bystartdate(a, b):
    if a.scheduled.start > b.scheduled.start:
        return 1
    if a.scheduled.start < b.scheduled.start:
        return -1
    return 0

def bystartdatekey(a):
    return a.scheduled.start

def bystartnodedatekey(a):
    n = a['node']
    dt = a['ts']
    #dt = n.scheduled.start
    if(isinstance(dt,orgdate.OrgDate)):
        dt=dt.start
    if(isinstance(dt, datetime.date)):
        return datetime.datetime.combine(dt.today(), datetime.datetime.min.time())
    return dt

def getdatefromnode(n):
    dt = datetime.datetime.min
    timestamps = n.get_timestamps(active=True,point=True,range=True)
    if timestamps and len(timestamps) > 0:
        dt = timestamps[0]
    if n.deadline:
        dt = n.deadline.start
    if n.scheduled:
        dt = n.scheduled.start
    if n.closed:
        dt = n.closed
    if(isinstance(dt,orgdate.OrgDate)):
        dt=dt.start
    #if(isinstance(dt, datetime.datetime)):
    #    return datetime.datetime.combine(dt.date(), datetime.datetime.min.time())
    if(isinstance(dt, datetime.date)):
        return datetime.datetime.combine(dt, datetime.datetime.min.time())
    return dt

def getdate(a):
    n = a['node']
    return getdatefromnode(n)

def getsortkey(a):
    n = a['node']
    dt = getdatefromnode(n)
    result = 0
    if dt:
        result = (dt - datetime.datetime(1970, 1, 1)).total_seconds()
    result = int(result)
    result = float(result)
    result *= 1000
    # Include priority after datetime information
    if n and n.priority and n.priority != "":
        result += (ord(n.priority[0]) - ord('A'))*100
    else:
        result += 0
    # Ensure todos end up before done before archived
    if IsTodo(n):
        result += 1
    if IsDone(n):
        result += 2
    if IsArchived(n):
        result += 3
    return result

def truncate_date_from_filename(filename):
    return filename[11:] if re.match(r"\A\d\d\d\d-\d\d-\d\d_", filename) else filename

# ============================================================
class WeekView(AgendaBaseView):
    def __init__(self, name, setup=True,**kwargs):
        super(WeekView, self).__init__(name, setup, **kwargs)


    def HighlightTime(self, date):
        reg = self.DateToRegion(date)
        style = self.dayhighlight
        if(style == None):
            style = "orgdatepicker.monthheader"
        self.output.add_regions("curweek",[reg],style,"",sublime.DRAW_NO_OUTLINE)

    def InsertTimeHeading(self, edit, hour):
        self.startOffset = 9
        self.cellSize    = 5
        pt = self.view.size()
        row, c = self.view.rowcol(pt)
        dayStart = sets.Get("agendaDayStartTime",6)
        dayEnd   = sets.Get("agendaDayEndTime",19)
        if(dayEnd > 23):
            dayEnd = 23
        if(dayStart < 0):
            dayStart = 0
        if(dayStart > dayEnd):
            dayStart = 0
            dayEnd   = 23
        header = "     "
        for i in range(dayStart, dayEnd+1):
            if(i == 10):
                header += " "
            header += "   {:2d}".format(i)
        header +="  \n"
        #header = "         0    1    2    3    4    5    6    7    8    9    10   11   12   13   14   15   16   17   18   19   20   21   22   23  \n"
        self.view.insert(edit, self.view.size(), header)
        dayStart = sets.Get("agendaDayStartTime",6)
        col = self.startOffset + (hour-dayStart)*self.cellSize
        s = self.view.text_point(row,col)
        e = self.view.text_point(row,col+2)
        reg = sublime.Region(s, e)
        style = "orgagenda.now"
        self.view.add_regions("curw",[reg],style,"",sublime.DRAW_NO_OUTLINE)

    def InsertDay(self, name, date, edit):
        pt = self.view.size()
        row, c = self.view.rowcol(pt)
        if(date.day == datetime.datetime.now().day):
            if(date.day == self.selected_date.day):
                self.view.insert(edit, self.view.size(),"@" + name + " " + "{0:2}".format(date.day) + "W[")
            else:
                self.view.insert(edit, self.view.size(),"#" + name + " " + "{0:2}".format(date.day) + "W[")
        elif(date.day == self.selected_date.day):
            self.view.insert(edit, self.view.size(),"&" + name + " " + "{0:2}".format(date.day) + "W[")
        else:
            self.view.insert(edit, self.view.size()," " + name + " " + "{0:2}".format(date.day) + "W[")

        daydata = []
        for entry in self.entries:
            n = entry['node']
            timestamps = n.get_timestamps(active=True,point=True,range=True)
            shouldContinue = False
            for t in timestamps:
                if(t.start.day == date.day and t.start.month == date.month and t.start.year == date.year):
                    daydata.append(entry)
                    entry['ts'] = t
                    shouldContinue = True
                    break
            if(shouldContinue):
                continue
            if(n.scheduled and (EnsureDate(n.scheduled.start) < EnsureDate(date) and not IsDone(n) and not IsArchived(n) or EnsureDate(n.scheduled.start) == EnsureDate(date))):
                daydata.append(entry)
                entry['ts'] = n.scheduled
                continue
            if(n.deadline and (EnsureDate(display_deadline_start(n.deadline)) < EnsureDate(date) and not IsDone(n) and not IsArchived(n) or EnsureDate(display_deadline_start(n.deadline)) == EnsureDate(date))):
                daydata.append(entry)
                entry['ts'] = n.deadline
                continue
        daydata.sort(key=bystartnodedatekey)

        lastMatchStart = 0
        lastMatch      = None
        lastMatchEntry = None
        matchCount     = 0
        doneMatchCount = 0
        dayStart = sets.Get("agendaDayStartTime",6)
        dayEnd   = sets.Get("agendaDayEndTime",19)
        if(dayEnd > 23):
            dayEnd = 23
        if(dayStart < 0):
            dayStart = 0
        if(dayStart > dayEnd):
            dayStart = 0
            dayEnd   = 23
        for hour in range(dayStart,dayEnd+1):
            for minSlot in range(0,self.cellSize):
                match = None
                matche = None
                for entry in daydata:
                    n = entry['node']
                    ts = IsInHourAndMinute(n, hour, minSlot*12, (minSlot+1)*12,date)
                    entry['ts'] = ts
                    if(ts):
                        match = n
                        matche = entry
                if(lastMatch != match and lastMatch != None):
                    s = self.view.text_point(row,lastMatchStart)
                    e = self.view.text_point(row,self.startOffset + (hour-dayStart)*self.cellSize + minSlot)
                    reg = sublime.Region(s, e)
                    if(IsDone(lastMatch) or IsArchived(lastMatch)):
                        style = "orgagenda.week.done." + str(doneMatchCount)
                        doneMatchCount = (doneMatchCount + 1) % 2
                        self.MarkEntryAtRegion(lastMatchEntry,reg)
                        self.view.add_regions("week_done_" + str(date.day) + "_" + str(hour) + "_" + str(minSlot),[reg],style,"", sublime.DRAW_SQUIGGLY_UNDERLINE)
                    else:
                        style = "orgagenda.week." + str(matchCount)
                        matchCount = (matchCount + 1) % 10
                        self.MarkEntryAtRegion(lastMatchEntry,reg)
                        self.view.add_regions("week_" + str(date.day) + "_" + str(hour) + "_" + str(minSlot),[reg],style,"",sublime.DRAW_NO_FILL)
                if(match != None):
                    if(lastMatch != match):
                        lastMatch      = match
                        lastMatchEntry = matche
                        lastMatchStart = self.startOffset + (hour-dayStart)*self.cellSize + minSlot
                    d = distanceFromStart(matche, hour, minSlot)
                    # If the time slot is larger than the name we space pad it
                    c = " "
                    if(d < len(match.heading) and d >= 0):
                        c = match.heading[d:d+1]
                    self.view.insert(edit, self.view.size(), c)
                else:
                    if(lastMatch != match):
                        lastMatch      = match
                        lastMatchStart = self.startOffset + (hour-dayStart)*self.cellSize + minSlot
                        lastMatchEntry = matche
                    if(minSlot < 4):
                        self.view.insert(edit, self.view.size(), ".")
                    else:
                        self.view.insert(edit, self.view.size(), "_")
        self.view.insert(edit, self.view.size(),"]\n")

    def RenderView(self, edit, clear=False):
        self.InsertAgendaHeading(edit)
        self.InsertTimeHeading(edit,self.selected_date.hour)
        #print(str(self.selected_date))
        wday   = self.selected_date.weekday()
        firstDayIndex = sets.GetWeekdayIndexByName(sets.Get("firstDayOfWeek", "Sunday"))
        wstart = self.selected_date + datetime.timedelta(days=firstDayIndex-wday)
        dayNames  = sets.Get("weekViewDayNames",["Mon", "Tue", "Wed", "Thr", "Fri", "Sat", "Sun"])
        numDays   = sets.Get("agendaWeekViewNumDays",7)
        for i in range(0,numDays):
            index = (firstDayIndex + i) % len(dayNames)
            self.InsertDay(dayNames[index], wstart + datetime.timedelta(days=i), edit)

    def FilterEntry(self, n, filename):
        return (not self.onlyTasks or (IsTodo(node) or IsDone(n))) and not IsProject(n) and HasTimestamp(n)

# ============================================================
class AgendaDayView(AgendaBaseView):
    def __init__(self, name, setup=True, skip_heading=False, **kwargs):
        self.skip_heading = skip_heading
        super(AgendaDayView, self).__init__(name, setup, **kwargs)

    def RenderDateHeading(self, edit, now):
        headerFormat = sets.Get("agendaHeaderFormat","%A \t%d %B %Y")
        self.view.insert(edit, self.view.size(), now.strftime(headerFormat) + "\n")

    def BuildAgendaEntry(self, filename, n, h, ts):
        start_minute = ts.minute if IsRawDate(ts) else ts.start.minute
        start = "{0:02d}:{1:02d}".format(h, start_minute)
        end = " -----"
        if not IsRawDate(ts) and ts.end:
            end = "-{0:02d}:{1:02d}".format(ts.end.hour, ts.end.minute)
        heading = n.todo + " " + n.heading if n.todo else n.heading
        file = truncate_date_from_filename(filename)
        return "{file:12} {start}{end} {heading:32} {deadline}".format(file=file[:12], start=start, end=end, heading=heading[:32], deadline=self.BuildDeadlineDisplay(n))

    def RenderAgendaAllDayEntry(self, edit, filename, n):
        heading = n.todo + " " + n.heading if n.todo else n.heading
        file = truncate_date_from_filename(filename)
        entry = "{file:12}             {heading:48} {deadline}\n".format(file=file[:12], heading=heading[:48], deadline=self.BuildDeadlineDisplay(n))
        self.view.insert(edit, self.view.size(), entry)

    def BuildDeadlineDisplay(self, node):
        if node.deadline:
            if(EnsureDateTime(display_deadline_start(node.deadline)) <= self.selected_date):
                if(EnsureDateTime(node.deadline.start).date() < self.selected_date.date()):
                    return "D: Overdue"
                elif(EnsureDateTime(node.deadline.start).date() == self.selected_date.date()):
                    return "D: Due Today"
                else:
                    return "D:@" + str(EnsureDateTime(node.deadline.start).date())
        return ""

    def RenderView(self, edit, clear=False):
        if not self.skip_heading:
            self.InsertAgendaHeading(edit)
        self.RenderDateHeading(edit, self.selected_date)
        view = self.view
        dayStart = sets.Get("agendaDayStartTime",6)
        dayEnd   = sets.Get("agendaDayEndTime",19)
        now = datetime.datetime.now()
        any_remaining = False
        for entry in self.entries:
            n = entry['node']
            filename = entry['file'].AgendaFilenameTag()
            ts = IsAllDay(n, self.selected_date.date())
            if ts:
                entry['found'] = True
                self.MarkEntryAt(entry, ts)
                self.RenderAgendaAllDayEntry(edit, filename, n)
            else:
                any_remaining = True

        lines = []
        if self.selected_date.date() == now.date():
            lines.append((now.time(), "{0:12} {1:02d}:{2:02d} - - - - - - - - - - - - - - - - - - - - -".format("now =>", now.hour, now.minute)))
            if any_remaining:
                for h in range(dayStart, dayEnd):
                    lines.append((datetime.time(h), "{0:12} {1:02d}:00...... --------------------".format("", h)))
        for entry in self.entries:
            n = entry['node']
            filename = entry['file'].AgendaFilenameTag()
            if not 'found' in entry:
                entry['found'] = True
                for h in range(0, 24):
                    ts = IsInHour(n, h, self.selected_date)
                    if ts:
                        self.MarkEntryAt(entry, ts)
                        line = self.BuildAgendaEntry(filename, n, h, ts)
                        lines.append((ts.start.time(), line))
                        break

        view.insert(edit, view.size(), "".join([l + "\n" for (_t, l) in sorted(lines)]))

    def FilterEntry(self, node, file):
        return (not self.onlyTasks or IsTodo(node)) and not IsDone(node) and not IsArchived(node) and IsOnDate(node, self.selected_date)

# ============================================================
class AgendaView(AgendaDayView):
    def __init__(self, name, setup=True, **kwargs):
        super(AgendaView, self).__init__(name, setup, **kwargs)
        self.blocks = [None,None,None,None,None,None,None]
        self.sym     = ("$","@","!","#","%","^","&")
        self.symUsed = [-1,-1,-1,-1,-1,-1,-1]

    def RenderDateHeading(self, edit, now):
        headerFormat = sets.Get("agendaHeaderFormat","%A \t%d %B %Y")
        self.view.insert(edit, self.view.size(), now.strftime(headerFormat) + "\n")

    def BuildHabitDisplay(self, n):
        if(n.scheduled and n.get_property("STYLE",None)):
            #OrgDateRepeatedTask
            repeats = n.repeated_tasks
            habitbar = "[_____________________]"
            hb = list(habitbar)
            # not schedule but done
            # not schedule not done
            # scheduled but not done
            # scheduled but last day
            # late
            # done
            if(repeats):
                start = self.selected_date-drel.relativedelta(days=20)
                cur = self.selected_date-drel.relativedelta(days=21)
                while(cur < self.selected_date):
                   cur = n.scheduled.next_repeat_from(cur)
                   if(cur < self.selected_date):
                        diff = (self.selected_date - cur).days
                        diff = 21 - diff
                        hb[diff] = '.'
                for i in range(0,21):
                    cur = start + drel.relativedelta(days=i)
                    for r in repeats:
                        if r.has_overlap(cur):
                            hb[i+1] = '*'
                pass
            return "H" + ''.join(hb)
        return ""

    def GetUnusedSymbol(self, blk):
        start = 0
        for i in range(0,len(self.symUsed)):
            if(self.symUsed[i] >= 0):
                start = i
                break
        for i in range(start,len(self.symUsed)):
            if(self.symUsed[i] < 0):
                self.symUsed[i] = blk
                return i
        return -1

    def ReleaseSymbol(self, blk):
        for i in range(0,len(self.symUsed)):
            if(self.symUsed[i] == blk):
                self.symUsed[i] = -1
                break

    def FindSymbol(self, blk):
        for i in range(0,len(self.symUsed)):
            if(self.symUsed[i] == blk):
                return i
        return 0


    def ClearAgendaBlocks(self,h):
        for i in range(0, len(self.blocks)):
            n = self.blocks[i]
            if(not IsInHour(n, h,self.selected_date)):
                self.ReleaseSymbol(i)
                self.blocks[i] = None

    def UpdateWithThisBlock(self, n, h):
        idx = -1
        for i in range(0, len(self.blocks)):
            if(idx == -1 and self.blocks[i] == None):
                idx = i
            if(self.blocks[i] == n):
                idx = -1
                return i
        if(idx != -1):
            self.blocks[idx] = n
            return idx
        return 0

    def GetAgendaBlocks(self,n,h):
        out = ""
        if n:
            symIdx = self.GetUnusedSymbol(0)
            self.ClearAgendaBlocks(h)
            myIdx = self.UpdateWithThisBlock(n, h)
            self.symUsed[symIdx] = myIdx
        else:
            self.ClearAgendaBlocks(h)
        spaceSym = "."
        for i in range(0, len(self.blocks)):
            if self.blocks[i]:
                spaceSym = " "
        if spaceSym == ".":
            out = ".."
        for i in range(0, len(self.blocks)):
            if not self.blocks[i]:
                out += spaceSym
            else:
                symIdx = self.FindSymbol(i)
                out += self.sym[symIdx]
        return out

    def RenderAgendaEntry(self,edit,filename,n,h,ts):
        start_minute = ts.minute if IsRawDate(ts) else ts.start.minute
        start = "{0:02d}:{1:02d}".format(h, start_minute)
        heading = n.todo + " " + n.heading if n.todo else n.heading
        file = truncate_date_from_filename(filename)
        entry = "{file:12} {start}B[{blocks}] {heading:32} {deadline}{habits}\n".format(file=file[:12], start=start, heading=heading[:32], deadline=self.BuildDeadlineDisplay(n), habits=self.BuildHabitDisplay(n), blocks=self.GetAgendaBlocks(n,h))
        self.view.insert(edit, self.view.size(), entry)

    def RenderAgendaAllDayEntry(self, edit, filename, n):
        heading = n.todo + " " + n.heading if n.todo else n.heading
        file = truncate_date_from_filename(filename)
        entry = "{file:12}                 {heading:48} {deadline}{habits}\n".format(file=file[:12], heading=heading[:48], deadline=self.BuildDeadlineDisplay(n), habits=self.BuildHabitDisplay(n))
        self.view.insert(edit, self.view.size(), entry)

    def RenderView(self, edit, clear=False):
        self.InsertAgendaHeading(edit)
        self.RenderDateHeading(edit, self.selected_date)
        view     = self.view
        dayStart = sets.Get("agendaDayStartTime",6)
        dayEnd   = sets.Get("agendaDayEndTime",19)
        allDat = []
        before = True
        now = datetime.datetime.now()
        for entry in self.entries:
            n = entry['node']
            filename = entry['file'].AgendaFilenameTag()
            ts = IsAllDay(n, self.selected_date.date())
            if ts:
                entry['found'] = 'f'
                self.MarkEntryAt(entry, ts)
                self.RenderAgendaAllDayEntry(edit, filename, n)
        for h in range(dayStart, dayEnd):
            didNotInsert = True
            if self.selected_date.date() == now.date() and now.hour == h:
                foundItems = []
                for entry in self.entries:
                    n = entry['node']
                    ts = IsInHour(n, h, self.selected_date)
                    if IsBeforeNow(ts, now) and ts:
                        entry['ts'] = ts
                        if not 'found' in entry:
                            foundItems.append(entry)
                            entry['found'] = 'b'
                foundItems.sort(key=bystartnodedatekey)
                for it in foundItems:
                    n = it['node']
                    ts = it['ts'] or n.scheduled
                    self.MarkEntryAt(it, ts)
                    self.RenderAgendaEntry(edit, it['file'].AgendaFilenameTag(), n, h, ts)
                    didNotInsert = False
                view.insert(edit, view.size(), "{0:12} {1:02d}:{2:02d} - - - - - - - - - - - - - - - - - - - - - \n".format("now =>", now.hour, now.minute) )
                foundItems = []
                for entry in self.entries:
                    n = entry['node']
                    ts = IsInHour(n, h, self.selected_date)
                    if IsAfterNow(ts, now) and ts:
                        entry['ts'] = ts
                        if not 'found' in entry or entry['found'] == 'b':
                            foundItems.append(entry)
                            entry['found'] = 'a'
                foundItems.sort(key=bystartnodedatekey)
                for it in foundItems:
                    n = it['node']
                    ts = it['ts'] or n.scheduled
                    self.MarkEntryAt(it, ts)
                    self.RenderAgendaEntry(edit, it['file'].AgendaFilenameTag(), n, h, ts)
                    didNotInsert = False
                before = False
            else:
                for entry in self.entries:
                    n = entry['node']
                    filename = entry['file'].AgendaFilenameTag()
                    ts = IsInHour(n,h,self.selected_date)
                    if ts and (not 'found' in entry or (not before and entry['found'] == 'b')):
                        entry['found'] = 'b' if before else 'a'
                        self.MarkEntryAt(entry, ts)
                        self.RenderAgendaEntry(edit,filename,n,h,ts)
                        didNotInsert = False
            if didNotInsert:
                empty = " " * 12
                blocks = self.GetAgendaBlocks(None,h)
                sep = "."
                esep = " "
                if not '.' in blocks:
                    sep = "B["
                    esep = "] "
                view.insert(edit, view.size(), "{0:12} {1:02d}:00{3}{2}{4}--------------------\n".format(empty, h, blocks, sep, esep))
        view.insert(edit,view.size(),"\n")

RE_IN_OUT_TAG = re.compile('(?P<inout>[|+-])?(?P<tag>[^ ]+)')
# ================================================================================
class TodoView(AgendaBaseView):
    def GetParam(self, name, defaultVal, kwargs):
        v       = kwargs[name] if name in kwargs else None
        if isinstance(v,bool):
            v = ">10.10"
        return v

    def __init__(self, name, setup=True, **kwargs):
        super(TodoView, self).__init__(name, setup, **kwargs)
        self.showduration = self.GetParam("showduration","7.7",kwargs)
        self.showfilename = "15.15" if "hidefilename" not in kwargs else None
        self.showheading  = "hideheading" not in kwargs
        self.showstatus   = "11.11" if "hidestatus" not in kwargs else None
        self.showdate     = self.GetParam("showdate","15.15",kwargs)
        self.showtime     = self.GetParam("showtime","6.6",kwargs)
        self.showeffort   = self.GetParam("showeffort",">6.6",kwargs)
        self.showafter    = self.GetParam("showafter",">12.12",kwargs)
        self.showassigned = self.GetParam("showassigned",">12.12", kwargs)
        self.showid       = self.GetParam("showid",">10.10", kwargs)
        self.showtotalduration = "showtotalduration" in kwargs
        self.byproject    = "byproject" in kwargs
        self.input        = None
        self.search_filter = None
        self.havesortorder = "sortascend" in kwargs or "sortdescend" in kwargs
        self.sortorder = False
        if self.havesortorder:
            self.sortorder = False if "sortascend" in kwargs else self.sortorder
            self.sortorder = True  if "sortdescend" in kwargs else self.sortorder

    def GetFormatHeaders(self, n, filename):
        data = {}
        data2 = {}
        if self.showfilename:
            data['filename'] = "File"
            data2['filename'] = "---------------"
        if self.showstatus:
            data['status']   = "Status"
            data2['status']   = "-----------"
        if self.showduration:
            data['duration'] = "Duration"
            data2['duration']= "--------"
        if self.showheading:
            data['heading'] =  "Heading"
            data2['heading'] = "--------------------"
        if self.showdate:
            data['date'] = "Date"
            data2['date']= "---------------"
        if self.showtime:
            data['time'] = "Time"
            data2['time'] = "------"
        if self.showeffort:
            data['effort'] = "Effort"
            data2['effort'] = "------"
        if self.showafter:
            data['after'] = "After"
            data2['after'] = "------------"
        if self.showid:
            data['id'] = "ID"
            data2['id'] = "-----------------"
        if self.showassigned:
            data['assigned'] = "Who"
            data2['assigned'] = "------------"
        return (data, data2)




    def GetFormatData(self, n, filename):
        data = {}
        if self.showfilename:
            data['filename'] = filename
        if self.showstatus:
            todo = ""
            if n:
                todo = n.todo
                if todo == None:
                    todo = ""
            data['status'] = todo
        if self.showduration:
            duration = ""
            dur = datetime.timedelta(days=0)
            if n:
                for c in n.clock:
                    dur += c.duration
                self.totalduration += dur
                duration = orgdate.OrgDate.format_duration(dur)
            data['duration'] = duration
        if self.showheading:
            heading = ""
            if n:
                heading = n.heading
            data['heading'] = heading
        if self.showdate:
            date = ""
            if n:
                dt = getdatefromnode(n)
                if dt != datetime.datetime.min and dt.date() != datetime.date.min:
                    date = dt.strftime("%Y-%m-%d %a")
            data['date'] = date
        if self.showtime:
            time = ""
            if n:
                dt = getdatefromnode(n)
                if dt != datetime.datetime.min and dt.time() != datetime.time.min:
                    time = dt.time().strftime("%H:%M")
            data['time'] = time
        if self.showeffort:
            effort = ""
            if n:
                effort = n.get_property("EFFORT","")
            data['effort'] = effort
        if self.showafter:
            after = ""
            if n:
                after = n.get_property("AFTER","")
            data['after'] = after
        if self.showid:
            idd = ""
            if n:
                idd = n.get_property("ID","")
                if not idd or idd == "":
                    idd = n.get_property("CUSTOM_ID","")
            data['id'] = idd
        if self.showassigned:
            ass = ""
            if n:
                ass = n.get_property("ASSIGNED","")
            data['assigned'] = ass
        return data


    def GetF(self,p,name):
        if p:
            return "{" + name + ":" + p + "} "
        return ""
    def GetFormatString(self):
        formatstr = ""
        formatstr += self.GetF(self.showfilename,"filename")
        formatstr += self.GetF(self.showstatus,"status")
        formatstr += self.GetF(self.showduration,"duration")
        formatstr += self.GetF(self.showdate,"date")
        formatstr += self.GetF(self.showtime,"time")
        formatstr += self.GetF(self.showeffort,"effort")
        formatstr += self.GetF(self.showafter,"after")
        formatstr += self.GetF(self.showid,"id")
        formatstr += self.GetF(self.showassigned,"assigned")
        if self.showheading:
            formatstr += "{heading}"
        formatstr += "\n"
        return formatstr

    def OnFilter(self, text):
        #print("FILTER: " + str(text))
        self.input.onRecalc = evt.Make(self.OnFilter)
        if text == None:
            self.search_filter = None
        else:
            self.search_filter = re.compile(text + ".*")
        self.view.run_command("org_agenda_re_render_view")

    def OpenFilterView(self):
        if self.input != None:
            self.input.onRecalc = evt.Make(self.OnFilter)
            self.input.run("Filter:",None,evt.Make(self.OnFilter))

    def getSortOrdering(self):
        order = not sets.Get("agendaTodoSortAscending",True)
        if self.havesortorder:
            order = self.sortorder
        return order

    def RenderView(self, edit, clear = False):
        self.ClearEntriesAt()
        if clear:
            self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.InsertAgendaHeading(edit)
        formatstr = self.GetFormatString()
        data,under      = self.GetFormatHeaders(None,"")
        self.view.insert(edit, self.view.size(), formatstr.format(**data))
        self.view.insert(edit, self.view.size(), formatstr.format(**under))
        self.totalduration = datetime.timedelta(days=0)
        if self.byproject:
            projects   = {}
            loosetasks = []
            for entry in self.entries:
                n        = entry['node']
                if n == None:
                    continue
                if self.search_filter and not n.is_root() and not self.search_filter.match(n.heading):
                    continue
                if n.is_root() or n.parent == None or n.parent.is_root() or not IsProject(n.parent):
                    loosetasks.append(entry)
                else:
                    pname = n.parent.heading
                    if pname not in projects:
                        projects[pname] = []
                    projects[pname].append(entry)
            for pname,vals in projects.items():
                vals.sort(key=getsortkey,reverse=self.getSortOrdering())
                projects[pname] = vals

            for pname,vals in projects.items():
                self.view.insert(edit, self.view.size(), "\n== [{0}] ==\n".format(pname))
                for entry in vals:
                    n        = entry['node']
                    filename = entry['file'].AgendaFilenameTag()
                    self.MarkEntryAt(entry)
                    self.RenderEntry(n, filename, edit)
            if len(loosetasks) > 0:
                self.view.insert(edit, self.view.size(), "\n== [] ==\n")
                for entry in loosetasks:
                    n        = entry['node']
                    filename = entry['file'].AgendaFilenameTag()
                    self.MarkEntryAt(entry)
                    self.RenderEntry(n, filename, edit)
        else:
            self.entries.sort(key=getsortkey,reverse=self.getSortOrdering())
            for entry in self.entries:
                n        = entry['node']
                if self.search_filter and not n.is_root() and not self.search_filter.match(n.heading):
                    continue
                filename = entry['file'].AgendaFilenameTag()
                self.MarkEntryAt(entry)
                self.RenderEntry(n, filename, edit)
        if self.showtotalduration:
            formatstr = self.GetFormatString()
            data      = self.GetFormatData(None,"")
            data['duration'] = orgdate.OrgDate.format_duration(self.totalduration)
            data['filename'] = "TOTAL: "
            self.view.insert(edit, self.view.size(), "----------------------------------------------------------------------------\n")
            self.view.insert(edit, self.view.size(), formatstr.format(**data))
        if(self.input == None):
            self.input = insSel.OrgInput()
            shouldFilter = sets.Get("agendaTodoFilterByDefault", False)
            if shouldFilter:
                self.OpenFilterView()

    def RenderEntry(self, n, filename, edit):
        formatstr = self.GetFormatString()
        data      = self.GetFormatData(n, filename)
        self.view.insert(edit, self.view.size(), formatstr.format(**data))

    def FilterEntry(self, n, filename):
        return IsTodo(n) and not IsProject(n) and not IsArchived(n)

# ================================================================================
class ProjectsView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(ProjectsView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return IsProject(n) and not IsArchived(n)

# ================================================================================
class NotBlockedProjectsView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(ProjectsView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return IsProject(n) and not IsBlockedProject(n) and not IsArchived(n)

# ================================================================================
class BlockedProjectsView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(BlockedProjectsView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return IsBlockedProject(n) and not IsArchived(n)

# ================================================================================
class LooseTasksView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(LooseTasksView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        rc = IsTodo(n) and not IsProject(n) and not IsProjectTask(n) and not IsArchived(n)
        return rc


# ================================================================================
class DoneTasksView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(DoneTasksView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        rc = IsDone(n) and not IsArchived(n)
        return rc

# ================================================================================
class ClockedView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(ClockedView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return n and n.clock

# ================================================================================
class HasStatusView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(HasStatusView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return n and (IsDone(n) or IsTodo(n))

# ================================================================================
class NextTasksProjectsView(TodoView):
    def __init__(self, name, setup=True, **kwargs):
        super(NextTasksProjectsView, self).__init__(name, setup, **kwargs)

    # TODO Print project and then the next task
    def RenderView(self, edit, clear=False):
        self.InsertAgendaHeading(edit)
        newEntries = []
        for entry in self.entries:
            n        = entry['node']
            filename = entry['file'].AgendaFilenameTag()
            self.MarkEntryAt(entry)
            self.view.insert(edit, self.view.size(), "{0:15} {1:12} {2}\n".format(filename,"-------", n.heading))
            #self.RenderEntry(n, filename, edit)
            for c in n.children:
                if(c.todo and c.todo == "NEXT"):
                    nentry = {'node': c, 'file': entry['file']}
                    newEntries.append(nentry)
                    self.MarkEntryAt(nentry)
                    self.view.insert(edit, self.view.size(), "{0:15} {1:12} {2}\n".format(" ", c.todo, c.heading))
                    #self.RenderEntry(c, filename, edit)
        for e in newEntries:
            self.entries.append(e)

    def FilterEntry(self, n, filename):
        return IsProject(n) and not IsBlockedProject(n) and not IsArchived(n)


# ================================================================================
class NoteView(TodoView):
    def __init__(self, name, setup=True,**kwargs):
        super(NoteView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return IsNote(n) and not IsProject(n) and not IsProjectTask(n) and not IsArchived(n)

# ================================================================================
class PhoneView(TodoView):
    def __init__(self, name, setup=True,**kwargs):
        super(PhoneView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return IsPhone(n) and not IsProject(n) and not IsProjectTask(n) and self.MatchTags(n) and not IsArchived(n)

# ================================================================================
class MeetingView(TodoView):
    def __init__(self, name, setup=True,**kwargs):
        super(MeetingView, self).__init__(name, setup, **kwargs)

    def FilterEntry(self, n, filename):
        return IsMeeting(n) and not IsProject(n) and not IsProjectTask(n) and self.MatchTags(n) and not IsArchived(n)

# ================================================================================
class CompositeViewListener(sublime_plugin.ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        # 4095 seems to crash when querying settings
        if(int(sublime.version()) != 4095):
            try:
                return "orgagenda" in settings.get("color_scheme","not here")
            except:
                return False
        return False

    def __init__(self, view):
        super(CompositeViewListener, self).__init__(view)
        self.agenda = FindMappedView(self.view)
        self.phantoms = []

    def clear_phantoms(self):
        for f in self.phantoms:
            self.view.erase_phantoms(f)
        self.phantoms = []

    def on_hover_done(self):
        self.clear_phantoms()

    def on_hover(self, point, hover_zone):
        if(not hasattr(self,'agenda') or self.agenda == None):
            return
        if(hover_zone == sublime.HOVER_TEXT):
            row,col = self.view.rowcol(point)
            n, f = self.agenda.At(row, col)
            if(n and f):
                self.clear_phantoms()
                line = self.view.line(point)
                reg = sublime.Region(point, line.end())
                body = """
                <body id="agenda-week-popup">
                <style>
                    div.block {{
                        display: block;
                        background-color: #333333;
                        border-style: solid;
                        border: 1px;
                        border-color: #666666;
                    }}
                    div.heading {{
                        color: #880077;
                        padding: 5px;
                        font-size: 20px;
                        font-weight: bold;
                        }}
                    div.file {{
                        color: grey;
                        padding-left: 5px;
                        font-size: 15px;
                        }}
                </style>
                <div class="block"/>
                <div class="heading">{0}</div>
                <div class="file">{1}</div>
                </div>
                </body>
                """.format(n.heading,f.filename)

                self.view.add_phantom(n.heading, reg, body, sublime.LAYOUT_INLINE)
                #sublime.Phantom(sublime.Region(point, point), "<html><body>" + n.heading + "</body></html>",sublime.LAYOUT_INLINE, None)
                self.phantoms.append(n.heading)
                print(n.heading)
                sublime.set_timeout(self.on_hover_done, 1000*2)

# ================================================================================
# ORG has this custom composite view feature.
# I want that. Make a view up of a couple of views.
class CompositeView(AgendaBaseView):
    def __init__(self, name, views):
        self.agendaViews = views
        super(CompositeView, self).__init__(name)
        self.SetupView()

    def RenderView(self, edit, clear=False):
        first = True
        for v in self.agendaViews:
            if not first:
                self.view.insert(edit, self.view.size(), ("=" * 75) + "\n")
            first = False
            v.view = self.view
            v.RenderView(edit, clear)
        # These get updated when rendered
        self.entries = []
        for v in self.agendaViews:
            self.entries += v.entries

    def InitializeSelectedDate(self, selected=None):
        AgendaBaseView.InitializeSelectedDate(self, selected)
        for v in self.agendaViews:
            v.InitializeSelectedDate(selected)

    def UpdateSelectedDate(self, date):
        self.selected_date = date
        for v in self.agendaViews:
            v.UpdateSelectedDate(date)

    def LoadAndFilterEntries(self):
        AgendaBaseView.LoadAndFilterEntries(self)
        for v in self.agendaViews:
            v.set_entries_filtered(self.entries)

    def At(self, row, col):
        for av in self.agendaViews:
            n, f  = av.At(row,col)
            if(n and f):
                return (n,f)
        return (None, None)

class AgendaWeekView(AgendaBaseView):
    def __init__(self, name, setup=True, **kwargs):
        self.day_views = [
            AgendaDayView("Monday", setup=False, skip_heading=True, **kwargs),
            AgendaDayView("Tuesday", setup=False, skip_heading=True, **kwargs),
            AgendaDayView("Wednesday", setup=False, skip_heading=True, **kwargs),
            AgendaDayView("Thursday", setup=False, skip_heading=True, **kwargs),
            AgendaDayView("Friday", setup=False, skip_heading=True, **kwargs),
            AgendaDayView("Saturday", setup=False, skip_heading=True, **kwargs),
            AgendaDayView("Sunday", setup=False, skip_heading=True, **kwargs),
        ]
        super(AgendaWeekView, self).__init__(name, setup, **kwargs)
        if setup:
            self.SetupView()

    def RenderView(self, edit, clear=False):
        self.InsertAgendaHeading(edit)
        for v in self.day_views:
            v.view = self.view
            v.RenderView(edit, clear)
        self.entries = []
        for v in self.day_views:
            self.entries += v.entries

    def InitializeSelectedDate(self, selected=None):
        AgendaBaseView.InitializeSelectedDate(self, selected)
        for (idx, v) in enumerate(self.day_views):
            v.InitializeSelectedDate(self.selected_date + datetime.timedelta(days=idx - self.selected_date.weekday()))

    def UpdateSelectedDate(self, date):
        self.selected_date = date
        for (idx, v) in enumerate(self.day_views):
            v.UpdateSelectedDate(self.selected_date + datetime.timedelta(days=idx - self.selected_date.weekday()))

    def LoadAndFilterEntries(self):
        AgendaBaseView.LoadAndFilterEntries(self)
        for v in self.day_views:
            v.set_entries_filtered(self.entries)

    def set_entries_filtered(self, entries):
        AgendaBaseView.set_entries_filtered(self, entries)
        for v in self.day_views:
            v.set_entries_filtered(self.entries)

    def At(self, row, col):
        for av in self.day_views:
            n, f  = av.At(row,col)
            if n and f:
                return (n,f)
        return (None, None)

    def FilterEntry(self, node, file):
        return (not self.onlyTasks or IsTodo(node)) and not IsDone(node) and not IsArchived(node) and HasTimestamp(node)

# ================================================================================
class OrgTodoViewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        todo = TodoView(TODO_VIEW)
        todo.DoRenderView(edit)

# ================================================================================
# Right now this is a composite view... Need to allow the user to define
# Their own versions of this.
class OrgAgendaDayViewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        pos = None
        if(self.view.name() == "Agenda"):
            pos = self.view.sel()[0]
        # Save and restore the cursor
        views = [CalendarView("Calendar",False), WeekView("Week", False), AgendaView("Agenda", False), BlockedProjectsView("Blocked Projects",False), NextTasksProjectsView("Next",False), LooseTasksView("Loose Tasks",False)]
        #views = [AgendaView("Agenda", False), TodoView("Global Todo List", False)]
        agenda = CompositeView("Agenda", views)
        #agenda = AgendaView(AGENDA_VIEW)
        agenda.DoRenderView(edit)
        if(self.view.name() == "Agenda"):
            agenda.RestoreCursor(pos)
        log.info("Day view refreshed")

# ================================================================================
# Goto the file in the current window (ENTER)
class OrgAgendaGoToCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        agenda = FindMappedView(self.view)
        if(agenda):
            row, col    = self.view.curRowCol()
            n, f = agenda.At(row, col)
            if(f):
                if(n):
                    path = "{0}:{1}".format(f.filename,n.start_row + 1)
                    self.view.window().open_file(path, sublime.ENCODED_POSITION)
            else:
                log.warning("COULD NOT LOCATE AGENDA ROW")

# ================================================================================
class RunEditingCommandOnNode:
    def __init__(self, view, command, params={}):
        self.view = view
        self.command = command
        self.params = params

    def onAfterSaved(self):
        pass # Not needed anymore

    def onSaved(self):
        whenDoneEvt = util.RandomString()
        evt.Get().once(whenDoneEvt, self.onAfterSaved)
        if self.viewName == "Agenda":
            self.view.run_command("org_agenda_day_view")
        else:
            self.view.run_command("org_agenda_custom_view", { "toShow": self.viewName, "onDone": whenDoneEvt })

    def onEdited(self):
        # NOTE the save here doesn't seem to be working
        # Not sure why. BUT...
        view = self.savedView
        view.run_command("save")
        sublime.set_timeout_async(lambda: self.onSaved(), 100)

    def onLoaded(self):
        view = self.savedView
        self.n.move_cursor_to(view)
        eventName = util.RandomString()
        evt.Get().once(eventName, self.onEdited)
        log.debug("Trying to run: " + self.command)
        params = {"onDone": eventName }
        for k,v in self.params.items():
            params[k] = v
        view.run_command(self.command, params)

    def Run(self):
        agenda = FindMappedView(self.view)
        if(agenda):
            self.viewName = agenda.name
            row, col  = self.view.curRowCol()
            self.row = row
            n, f = agenda.At(row,col)
            if(f):
                if(n):
                    self.n         = n
                    self.f         = f
                    self.savedView = get_view_for_silent_edit_file(f)
                    # Give time for the document to be opened.
                    sublime.set_timeout_async(lambda: self.onLoaded(), 200)
            else:
                log.warning("COULD NOT LOCATE AGENDA ROW")

# ================================================================================
class CalendarViewRegistry:
    def __init__(self):
        self.KnownViews = {}
        self.AddView("Calendar", CalendarView)
        self.AddView("Day", AgendaView)
        self.AddView("Day Agenda", AgendaDayView)
        self.AddView("Week Agenda", AgendaWeekView)
        self.AddView("Blocked Projects", BlockedProjectsView)
        self.AddView("Next Tasks", NextTasksProjectsView)
        self.AddView("Loose Tasks", LooseTasksView)
        self.AddView("Has Status", HasStatusView)
        self.AddView("Todos", TodoView)
        self.AddView("Notes", NoteView)
        self.AddView("Meetings", MeetingView)
        self.AddView("Phone", PhoneView)
        self.AddView("Week", WeekView)
        self.AddView("Done", DoneTasksView)
        self.AddView("Clocked", ClockedView)
        self.AddView("Projects", ProjectsView)
        self.AddView("Not Blocked Projects", NotBlockedProjectsView)

    def AddView(self,name,cls):
        self.KnownViews[name] = cls

    # ViewName: <NAME> <ARGS> : <NAME> <ARGS>
    def ParseArgs(self, n ):
        tokens = n.split(':')
        name = tokens[0].strip()
        args = {}
        i = 1
        while(i < len(tokens)):
            p = tokens[i].strip()
            if(len(p) > 0):
                idx = p.find(' ')
                if(idx > 0):
                    pname = p[:idx].strip()
                    pval = p[idx:].strip()
                    args[pname] = pval
                    #print(pname + " -> " + pval)
                else:
                    args[p] = True
            i += 1
        return (name, args)

    def CreateCompositeView(self,views,name="Agenda"):
        vlist = []
        for v in views:
            n, args = self.ParseArgs(v)
            vv = None
            if(args == None):
                vv = self.KnownViews[n](n, False)
            else:
                vv = self.KnownViews[n](n, False, **args)
            if(vv):
                vlist.append(vv)
        if len(vlist) == 1:
            vlist[0].name = name + " [" + vlist[0].name + "]"
        cview = CompositeView(name, vlist)
        return cview

viewRegistry = CalendarViewRegistry()


# ================================================================================
class OrgAgendaCustomViewCommand(sublime_plugin.TextCommand):
    def run(self, edit, toShow="Default", onDone=None):
        pos = None
        #if(self.view.name() == "Agenda"):
        pos = self.view.sel()[0]
        ReloadAllUnsavedBuffers()
        views = sets.Get("AgendaCustomViews",{ "Default": ["Calendar", "Week", "Day", "Blocked Projects", "Next Tasks", "Loose Tasks"]})
        views = views[toShow]
        nameOfShow = toShow
        if(toShow == "Default"):
            nameOfShow = "Agenda"
        agenda = viewRegistry.CreateCompositeView(views, nameOfShow)
        #agenda = CompositeView("Agenda", views)
        #agenda = AgendaView(AGENDA_VIEW)
        agenda.DoRenderView(edit)
        #if(self.view.name() == "Agenda"):
        agenda.RestoreCursor(pos)
        agenda.view.run_command("org_agenda_refresh")
        log.info("Custom view refreshed")
        evt.EmitIf(onDone)


# ================================================================================
# TODO: This is a work in progress that only lists them right now.
#       The goal is add parameters for filtered todos and support
#       multiple calendar views in the end. I should probably
#       rename the command above so we know it is intended to directly
#       select a view rather than use a quick panel.
#       I would like to add:
#       1. Vertical Week View (Org Style)
#       2. Horizontal Week View (Calendar Style)
#       3. Month View (Vim Style)
#       4. Month View Quick Highlight (My Style)
#       5. Various Filtered Todo lists.
#       6. Try an HTML version of a todo?
class OrgAgendaChooseCustomViewCommand(sublime_plugin.TextCommand):
    def on_done_st4(self,index,modifers):
        self.on_done(index)
    def on_done(self, index):
        if(index < 0):
            return
        key = self.keys[index]
        self.view.run_command("org_agenda_custom_view", { "toShow": key })
        evt.EmitIf(self.onDone)

    def run(self, edit, onDone=None):
        self.onDone = onDone
        self.views = sets.Get("AgendaCustomViews",{ "Default": ["Calendar", "Day", "Blocked Projects", "Next Tasks", "Loose Tasks"]})
        self.keys = list(self.views.keys())
        if(int(sublime.version()) <= 4096):
            self.view.window().show_quick_panel(self.keys, self.on_done, -1, -1)
        else:
            self.view.window().show_quick_panel(self.keys, self.on_done_st4, -1, -1)

# ================================================================================
# When the view filter changes we re-render the todo view.
class OrgAgendaReRenderViewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        v = FindMappedView(self.view)
        if v != None:
            v.DoRenderView(edit, True)

# ================================================================================
class OrgAgendaReOpenFilterViewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        v = FindMappedView(self.view)
        if v != None:
            v.OpenFilterView()

# ================================================================================
# Change the TODO status of the node.
class OrgAgendaChangeTodoCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view, "org_todo_change")
        self.ed.Run()


# ================================================================================
class OrgAgendaChangePriorityCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view, "org_priority_change")
        self.ed.Run()

# ================================================================================
class OrgAgendaScheduleCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_schedule")
        self.ed.Run()

# ================================================================================
class OrgAgendaDeadlineCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_deadline")
        self.ed.Run()

# ================================================================================
class OrgAgendaClockInCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_clock_in")
        self.ed.Run()

# ================================================================================
class OrgAgendaClockOutCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_clock_out")
        self.ed.Run()

# ================================================================================
class OrgAgendaInsertTagCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_insert_tag")
        self.ed.Run()

# ================================================================================
class OrgAgendaInsertEffortCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_insert_property", {"name": "EFFORT"})
        self.ed.Run()

# ================================================================================
class OrgAgendaAssignCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_insert_property", {"name": "ASSIGNED"})
        self.ed.Run()

# ================================================================================
class OrgAgendaIdCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.ed = RunEditingCommandOnNode(self.view,"org_insert_property", {"name": "ID"})
        self.ed.Run()

# ================================================================================
# Goto the file but in a split (SPACE)
class OrgAgendaGoToSplitCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        agenda = FindMappedView(self.view)
        if(agenda):
            row, col    = self.view.curRowCol()
            n, f = agenda.At(row,col)
            if(f):
                if(n):
                    path = "{0}:{1}".format(f.filename,n.start_row + 1)
                    newView = self.view.window().open_file(path, sublime.ENCODED_POSITION)
                    move_file_other_group(self.view, newView)
                    #sublime.set_timeout_async(lambda: move_file_other_group(self.view, newView), 100)
            else:
                log.warning("COULD NOT LOCATE AGENDA ROW")

# ================================================================================
class OrgTagFilteredTodoViewInternalCommand(sublime_plugin.TextCommand):
    def run(self,edit,tags):
        # TODO: add filtering to this and name it nicely
        ReloadAllUnsavedBuffers()
        todo = TodoView(TODO_VIEW + " Filtered By: " + tags,tagfilter=tags)
        todo.DoRenderView(edit)

# ================================================================================
class OrgTagFilteredTodoViewCommand(sublime_plugin.TextCommand):
    def run(self,edit):
        self.view.window().show_input_panel(
                    "Tags:",
                    "",
                    self.showTodos, None, None)

    def showTodos(self, tags):
        if(not tags):
            return
        self.view.run_command('org_tag_filtered_todo_view_internal', {"tags": tags})

# ================================================================================
class OrgAgendaGotoNextDayCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        agenda = FindMappedView(self.view)
        date = agenda.selected_date
        date = date + datetime.timedelta(days=1)
        agenda.UpdateSelectedDate(date)
        agenda.LoadAndFilterEntries()
        agenda.Clear(edit)
        agenda.DoRenderView(edit)

# ================================================================================
class OrgAgendaGotoPrevDayCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        agenda = FindMappedView(self.view)
        date = agenda.selected_date
        date = date + datetime.timedelta(days=-1)
        agenda.UpdateSelectedDate(date)
        agenda.LoadAndFilterEntries()
        agenda.Clear(edit)
        agenda.DoRenderView(edit)

class OrgAgendaRefreshCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        agenda = FindMappedView(self.view)
        date = agenda.selected_date
        agenda.UpdateSelectedDate(date)
        agenda.LoadAndFilterEntries()
        agenda.Clear(edit)
        agenda.DoRenderView(edit)
